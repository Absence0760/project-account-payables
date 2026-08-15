"""Digital signatures on invoice approvals (SOX non-repudiation).

A cryptographic "timestamp + user hash" token attached to every approval audit
row. It is an HMAC-SHA256 over a CANONICAL, deterministic serialization of the
approval facts — who approved (``actor_id``), what (``invoice_id`` + exact
``amount``), the ``decision``, and WHEN (``timestamp``). Because the key is held
only by the platform (sops + KMS in deployed envs), a row in the immutable
``audit_log`` can later be *re-derived* and bit-compared: any tamper with the
amount, the actor, or the timestamp changes the digest, so a verifier proves the
trail wasn't altered. This is non-repudiation, not encryption — the payload
fields themselves stay in the clear in the audit row.

Pure module: no DB, no network. The signing key comes from
``settings.approval_signing_key`` (``FEOH_APPROVAL_SIGNING_KEY``) — empty by
default, a NON-secret dev value committed in ``.env.development``, the real key
via sops in deployed envs. There is NO hardcoded production fallback; an empty
key yields an empty signature (signing is skipped), mirroring
``webhook_security.verify_hmac_sha256``'s fail-closed posture.

Money is exact: ``amount`` is serialised as a normalized string-``Decimal``
(never float) so a re-derivation on the verify side reproduces the exact same
canonical bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

# The HMAC algorithm label persisted alongside the digest, so a future
# key/algorithm migration can be versioned without re-reading every row blind.
SIGNATURE_ALG = "HMAC-SHA256"

#: Verdicts :func:`check_approval_row` can return for one approval audit row.
#: ``unsigned`` is deliberately distinct from ``invalid``: a row written before
#: signing was switched on has nothing to verify, whereas a row that carries a
#: digest which no longer re-derives is evidence of a tamper.
VERDICT_VALID = "valid"
VERDICT_INVALID = "invalid"
VERDICT_UNSIGNED = "unsigned"

# Canonical, stable order of the fields that go into the signed payload. The
# order is load-bearing — both sign and verify must serialise identically — so
# it is defined once here and never reordered.
SIGNED_FIELDS = ["invoice_id", "amount", "actor_id", "decision", "timestamp"]


def _canonical_amount(amount: Decimal) -> str:
    """Serialise money as an exact, normalized string (never float).

    ``Decimal("100.00")`` and ``Decimal("100.0")`` both normalise to the same
    canonical string so a trivially-different-but-equal amount can't shift the
    digest. Uses a fixed 2-dp quantize then ``str`` — invoice amounts are
    ``Numeric(15, 2)``, so 2dp is lossless.
    """
    return str(Decimal(str(amount)).quantize(Decimal("0.01")))


def _canonical_payload(
    *,
    invoice_id: uuid.UUID,
    amount: Decimal,
    actor_id: uuid.UUID,
    decision: str,
    timestamp: datetime,
) -> bytes:
    """Deterministic UTF-8 JSON serialization of the signed facts.

    ``sort_keys`` + the explicit per-field coercion below make the bytes
    reproducible across processes and Python versions. Timestamps are ISO-8601
    (timezone-aware) — the caller passes an aware ``datetime``.
    """
    payload = {
        "invoice_id": str(invoice_id),
        "amount": _canonical_amount(amount),
        "actor_id": str(actor_id),
        "decision": decision,
        "timestamp": timestamp.isoformat(),
    }
    # separators removes incidental whitespace; sort_keys pins key order even
    # though we already build the dict in canonical order — belt and braces.
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_approval(
    *,
    invoice_id: uuid.UUID,
    amount: Decimal,
    actor_id: uuid.UUID,
    decision: str,
    timestamp: datetime,
    signing_key: str,
) -> str:
    """Return the hex HMAC-SHA256 digest over the canonical approval payload.

    An empty ``signing_key`` returns ``""`` (signing skipped) — there is no
    hardcoded fallback key, matching the secrets invariant. A non-empty key
    always produces a 64-char lowercase hex digest.
    """
    if not signing_key:
        return ""
    msg = _canonical_payload(
        invoice_id=invoice_id,
        amount=amount,
        actor_id=actor_id,
        decision=decision,
        timestamp=timestamp,
    )
    return hmac.new(signing_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_approval(
    *,
    invoice_id: uuid.UUID,
    amount: Decimal,
    actor_id: uuid.UUID,
    decision: str,
    timestamp: datetime,
    signature: str | None,
    signing_key: str,
) -> bool:
    """Constant-time check that ``signature`` is the HMAC over these facts.

    Recomputes the digest from the (possibly-tampered) facts and compares it to
    the stored ``signature`` with ``hmac.compare_digest``. Returns ``False`` —
    never raises — on an empty key, an empty/missing signature, or any mismatch,
    so a tampered amount/actor/timestamp surfaces as ``valid: False`` rather than
    an error.
    """
    if not signing_key or not signature:
        return False
    try:
        expected = sign_approval(
            invoice_id=invoice_id,
            amount=amount,
            actor_id=actor_id,
            decision=decision,
            timestamp=timestamp,
            signing_key=signing_key,
        )
    except Exception:  # noqa: BLE001 — defensive: malformed facts fail closed
        return False
    return hmac.compare_digest(expected, signature)


def build_signature_detail(
    *,
    invoice_id: uuid.UUID,
    amount: Decimal,
    actor_id: uuid.UUID,
    decision: str,
    timestamp: datetime,
    signing_key: str,
) -> dict | None:
    """Build the ``details["signature"]`` block stored on the approval audit row.

    Returns ``None`` when no key is configured (so the audit row simply carries
    no signature block rather than an empty one). The block records the
    algorithm, the canonical ISO timestamp the digest was computed over, the
    list of signed field names (NOT their values — the amount value already
    lives elsewhere on the row; we keep the block PII-free), and the hex digest.
    """
    value = sign_approval(
        invoice_id=invoice_id,
        amount=amount,
        actor_id=actor_id,
        decision=decision,
        timestamp=timestamp,
        signing_key=signing_key,
    )
    if not value:
        return None
    return {
        "alg": SIGNATURE_ALG,
        "value": value,
        "signed_fields": SIGNED_FIELDS,
        "signed_at": timestamp.isoformat(),
    }


@dataclass(frozen=True)
class SignatureCheck:
    """Verdict for ONE ``invoice.approved`` audit row.

    ``signed_at`` is echoed back verbatim (the raw string off the row) so a
    caller can show what the row claims even when that claim is what failed to
    parse.
    """

    verdict: str
    signed_at: str | None = None

    @property
    def signed(self) -> bool:
        """The row carries a signature block at all."""
        return self.verdict != VERDICT_UNSIGNED

    @property
    def valid(self) -> bool:
        """The digest re-derived to the stored value."""
        return self.verdict == VERDICT_VALID


def check_approval_row(
    *,
    details: dict | None,
    invoice_id: uuid.UUID,
    amount: Decimal,
    actor_id: uuid.UUID | None,
    signing_key: str,
    decision: str = "approved",
) -> SignatureCheck:
    """Re-derive and compare the signature on one approval audit row.

    The single definition of "does this approval row still verify", shared by
    the per-invoice check (``GET /api/audit/invoice/{id}/verify-signatures``)
    and the period-wide population sweep (``GET /api/audit/verify-signatures``)
    so the two can never disagree about what a tampered row looks like.

    Pure — the caller supplies the row's ``details`` JSONB, the invoice's
    **current** exact ``amount`` (verifying against the live value is the whole
    point: a post-approval amount tamper must break the digest), and the row's
    ``actor_id``.

    Never raises: a malformed block, an unparseable ``signed_at``, a missing
    actor, or a bad digest all land on ``invalid`` — fail-closed, so a
    corrupted row surfaces as a finding rather than a 500 that hides it.
    """
    sig = (details or {}).get("signature")
    if not isinstance(sig, dict):
        return SignatureCheck(VERDICT_UNSIGNED)

    raw = sig.get("signed_at")
    # Only a string is echoed back — a non-string `signed_at` is itself
    # corruption, and the response contract keeps this field JSON-string-or-null.
    signed_at_raw = raw if isinstance(raw, str) else None
    signed_at: datetime | None = None
    if signed_at_raw is not None:
        try:
            signed_at = datetime.fromisoformat(signed_at_raw)
        except ValueError:
            signed_at = None

    if signed_at is None or actor_id is None:
        return SignatureCheck(VERDICT_INVALID, signed_at_raw)

    try:
        ok = verify_approval(
            invoice_id=invoice_id,
            amount=amount,
            actor_id=actor_id,
            decision=decision,
            timestamp=signed_at,
            signature=sig.get("value"),
            signing_key=signing_key,
        )
    except (InvalidOperation, ValueError):
        ok = False

    return SignatureCheck(VERDICT_VALID if ok else VERDICT_INVALID, signed_at_raw)
