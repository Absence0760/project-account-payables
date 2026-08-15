"""Signing-secret rotation: which secrets are live for a subscription right now.

A subscription's HMAC signing secret is the customer's verification key, and
until now it was minted once and never replaceable. Anyone holding it can forge
a signed ``invoice.approved`` / ``payment.settled`` payload into the customer's
receiver, so a leak needed a remedy — and the only one available was
``DELETE`` + re-create, which changes the subscription id and CASCADE-deletes
the whole delivery history.

The hard part of rotation is not minting a new secret; it is the instant
in between. With ONE signature header you cannot satisfy a receiver still
configured with the old secret and one already holding the new one at the same
time. So a rotation may open a bounded **overlap window** during which both
secrets sign, delivered in two headers:

``X-Webhook-Signature``
    always the CURRENT secret's signature. A receiver's existing contract never
    changes meaning — the primary header is always the live key.

``X-Webhook-Signature-Previous``
    the retiring secret's signature, present ONLY while the window is open.

A receiver that accepts *either* header rotates with zero dropped deliveries:
it can make that (additive, no-op) change at any time before rotating. A
receiver that reads only the primary header is no worse off than a hard swap —
it pastes the new secret and its downtime is bounded by how fast it does so.

Pure: no DB, no I/O. The caller owns persistence; this module owns the rules —
in particular the single rule that an EXPIRED window is indistinguishable from
no window at all, so a retired secret can never keep signing because a row was
left stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.services.webhooks.signing import generate_signing_secret

#: Default overlap if the caller doesn't choose one. Long enough for a human to
#: paste a secret into another system during a working session, short enough
#: that a retired key isn't left signing for days.
DEFAULT_OVERLAP_MINUTES = 60

#: Upper bound. The whole point of rotating is to stop honouring the old key;
#: a window measured in weeks would defeat that, so the API refuses it.
MAX_OVERLAP_MINUTES = 1440  # 24h

#: `0` is explicitly allowed and means "hard cutover, no overlap" — the right
#: choice when the old secret is known-compromised and must stop working NOW.
MIN_OVERLAP_MINUTES = 0

PRIMARY_SIGNATURE_HEADER = "X-Webhook-Signature"
PREVIOUS_SIGNATURE_HEADER = "X-Webhook-Signature-Previous"


@dataclass(frozen=True)
class RotationResult:
    """The new state of a subscription's secrets after a rotation.

    ``plaintext_secret`` is returned to the admin EXACTLY ONCE, mirroring the
    create-time contract. ``previous_secret`` / ``previous_expires_at`` are
    both ``None`` on a hard cutover.
    """

    plaintext_secret: str
    secret_prefix: str
    previous_secret: str | None
    previous_expires_at: datetime | None


def rotate_secret(
    *,
    current_secret: str,
    now: datetime,
    overlap_minutes: int = DEFAULT_OVERLAP_MINUTES,
) -> RotationResult:
    """Mint a replacement secret and decide the retiring secret's fate.

    ``overlap_minutes == 0`` retires the current secret immediately — nothing
    is carried over, so the previous key stops verifying on the very next
    delivery. Any positive value keeps it signing the secondary header until
    ``now + overlap_minutes``.

    Raises ``ValueError`` outside ``[MIN_OVERLAP_MINUTES, MAX_OVERLAP_MINUTES]``
    rather than clamping: silently shortening a window the caller asked for
    would drop deliveries they were relying on, and silently lengthening one
    would keep a key they wanted dead alive.
    """
    if not MIN_OVERLAP_MINUTES <= overlap_minutes <= MAX_OVERLAP_MINUTES:
        raise ValueError(
            f"overlap_minutes must be between {MIN_OVERLAP_MINUTES} and {MAX_OVERLAP_MINUTES}"
        )
    secret, prefix = generate_signing_secret()
    if overlap_minutes == MIN_OVERLAP_MINUTES:
        return RotationResult(
            plaintext_secret=secret,
            secret_prefix=prefix,
            previous_secret=None,
            previous_expires_at=None,
        )
    return RotationResult(
        plaintext_secret=secret,
        secret_prefix=prefix,
        previous_secret=current_secret,
        previous_expires_at=now + timedelta(minutes=overlap_minutes),
    )


def previous_secret_if_live(
    *,
    previous_secret: str | None,
    previous_expires_at: datetime | None,
    now: datetime | None = None,
) -> str | None:
    """The retiring secret, or ``None`` if it is no longer entitled to sign.

    The one rule this module exists to enforce, and the reason both the
    dispatcher and the API read it from here rather than each testing the
    columns themselves. A secret is live only when BOTH columns are populated
    AND the expiry is still in the future — so a half-written row, or a window
    nobody cleaned up, both read as "no previous secret" rather than leaving a
    retired key signing indefinitely.

    Naive datetimes are treated as UTC: the column is ``TIMESTAMPTZ``, but a
    value that has been through a driver or a test fixture can arrive without
    tzinfo, and comparing that against an aware ``now`` raises. Failing closed
    on the comparison would silently drop the overlap header mid-rotation.
    """
    if not previous_secret or previous_expires_at is None:
        return None
    moment = now or datetime.now(UTC)
    expires = previous_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return previous_secret if expires > moment else None
