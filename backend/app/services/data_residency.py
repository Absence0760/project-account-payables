"""Data residency — pin a tenant's DB + object storage to a geographic region.

GDPR / CCPA data-residency model for the database-per-tenant architecture.
Every tenant already gets its own Postgres database (``feoh_<slug>``) and its own
MinIO/S3 key prefix, so pinning a tenant's data to a region is a *placement*
decision, not a schema change: route that tenant's DB onto a regional cluster
and its object storage onto a regional bucket/endpoint. This module is the
single source of truth for which region a tenant is configured for and where —
documentation-level for now — each region's infra is intended to live.

The region is stored on the existing ``Organization.settings`` JSONB column at
``settings["residency"]["region"]`` (same pattern as ``settings.retention`` /
``settings.sso``) — **no new column, no migration**. ``DEFAULT_REGION`` is a
module constant, not an env var, so the single-region reality stays codified
here until multi-region infra ships.

Where the stack *actually* runs is the separate, operator-declared
``FEOH_DEPLOYED_REGION`` — a fact about the deployment, not about any tenant, so
it belongs in env rather than in a tenant's settings.
:func:`check_residency_alignment` compares the two and is **advisory only**: it
reports, and nothing in the request path may branch on its verdict.

See ``docs/data-residency.md`` for the full model and the future multi-region
plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.organization import Organization

logger = logging.getLogger(__name__)

# Supported residency regions. Each value is the public, stable region token the
# API accepts and persists; the human-readable meaning + intended placement live
# in REGION_PLACEMENT below.
#
#   us  — United States (default — where the single-region stack runs today)
#   eu  — European Union (GDPR data-residency: data kept within the EEA)
#   uk  — United Kingdom (post-Brexit UK GDPR; separate from `eu`)
#   ca  — Canada (PIPEDA / provincial residency requirements)
#   au  — Australia (Privacy Act / APP cross-border restrictions)
SUPPORTED_REGIONS: tuple[str, ...] = ("us", "eu", "uk", "ca", "au")

# Fallback when a tenant has set no residency region. The whole platform runs
# in a single region today, and `us` is where that region lives — so an org with
# no explicit pin is, in reality, US-resident.
DEFAULT_REGION: str = "us"

# Documentation-level placement model: per region, the intended tenant-DB
# host/cluster and object-storage (MinIO/S3) bucket + endpoint a tenant pinned
# to that region should be routed onto once multi-region infra exists. These are
# the target names the future provisioning + connection layer will resolve
# against; nothing here is wired into live infra yet (see the single-region
# reality note in docs/data-residency.md). Kept here so the "model" is explicit
# and reviewable before any cloud resource is created.
REGION_PLACEMENT: dict[str, dict[str, str]] = {
    "us": {
        "label": "United States",
        "db_cluster": "feoh-pg-us-east-1",
        "s3_bucket": "feoh-tenant-files-us",
        "s3_region": "us-east-1",
        "s3_endpoint": "https://s3.us-east-1.amazonaws.com",
    },
    "eu": {
        "label": "European Union",
        "db_cluster": "feoh-pg-eu-central-1",
        "s3_bucket": "feoh-tenant-files-eu",
        "s3_region": "eu-central-1",
        "s3_endpoint": "https://s3.eu-central-1.amazonaws.com",
    },
    "uk": {
        "label": "United Kingdom",
        "db_cluster": "feoh-pg-eu-west-2",
        "s3_bucket": "feoh-tenant-files-uk",
        "s3_region": "eu-west-2",
        "s3_endpoint": "https://s3.eu-west-2.amazonaws.com",
    },
    "ca": {
        "label": "Canada",
        "db_cluster": "feoh-pg-ca-central-1",
        "s3_bucket": "feoh-tenant-files-ca",
        "s3_region": "ca-central-1",
        "s3_endpoint": "https://s3.ca-central-1.amazonaws.com",
    },
    "au": {
        "label": "Australia",
        "db_cluster": "feoh-pg-ap-southeast-2",
        "s3_bucket": "feoh-tenant-files-au",
        "s3_region": "ap-southeast-2",
        "s3_endpoint": "https://s3.ap-southeast-2.amazonaws.com",
    },
}


def is_supported_region(region: str | None) -> bool:
    """True if ``region`` is one of the platform's supported residency regions."""
    return isinstance(region, str) and region in SUPPORTED_REGIONS


def resolve_region(org: Organization | None) -> str:
    """Return the tenant's configured residency region, or ``DEFAULT_REGION``.

    Reads ``org.settings["residency"]["region"]`` defensively — a missing
    settings dict, a missing/empty residency block, or a value that is no longer
    in ``SUPPORTED_REGIONS`` all fall back to the default. Never raises (a
    placement read must never break a request path).
    """
    if org is None:
        return DEFAULT_REGION
    settings_dict = getattr(org, "settings", None) or {}
    residency = settings_dict.get("residency")
    if not isinstance(residency, dict):
        return DEFAULT_REGION
    region = residency.get("region")
    if is_supported_region(region):
        return region  # type: ignore[return-value]
    return DEFAULT_REGION


def get_region_placement(region: str) -> dict[str, str]:
    """Return the documented placement for ``region`` (DB cluster + S3 target).

    Falls back to the default region's placement for an unknown key so callers
    always get a usable target rather than a KeyError — mirrors the fail-soft
    posture of ``resolve_region``.
    """
    return REGION_PLACEMENT.get(region, REGION_PLACEMENT[DEFAULT_REGION])


# The three alignment verdicts. `unknown` is a first-class state, not an error:
# the operator has not told us where the stack runs, so we cannot attest either
# way — and saying so is strictly better than the reassuring-but-unfounded
# "aligned" a defaulted comparison would produce.
ALIGNMENT_ALIGNED = "aligned"
ALIGNMENT_MISALIGNED = "misaligned"
ALIGNMENT_UNKNOWN = "unknown"

# Why an alignment verdict is `unknown`. Stable tokens (the UI maps them to
# copy); both are operator-configuration states, never tenant data.
REASON_DEPLOYED_REGION_UNSET = "deployed_region_unset"
REASON_DEPLOYED_REGION_UNRECOGNISED = "deployed_region_unrecognised"


@dataclass(frozen=True)
class ResidencyAlignment:
    """Verdict of the advisory configured-vs-deployed region comparison.

    ``aligned`` is deliberately tri-state (`True` / `False` / `None`): `None`
    goes with ``status == "unknown"`` so a caller that reads only this field
    can never mistake "we don't know" for "yes". ``reason`` names *why* it is
    unknown so the answer is actionable rather than merely honest.
    """

    status: str
    aligned: bool | None
    configured_region: str
    deployed_region: str | None
    reason: str | None = None


def check_residency_alignment(org: Organization, deployed_region: str | None) -> ResidencyAlignment:
    """Advisory check: is the tenant's configured region the one we're deployed in?

    Reports — **never blocks** — a mismatch between a tenant's *configured*
    residency region and the region the stack is *actually* deployed in. Until
    multi-region infra exists the whole platform is single-region, so a tenant
    pinned to `eu` while the stack runs in `us` is out of alignment and should be
    flagged for an operator (a data-residency commitment we're not yet
    honouring).

    ``deployed_region`` is operator configuration (``FEOH_DEPLOYED_REGION``), and
    two of its states are *not* a comparison:

    * **unset** — nobody has declared where this stack runs. Answering `aligned`
      by defaulting it to :data:`DEFAULT_REGION` would hand an EU tenant a green
      light nothing verified, so the verdict is ``unknown``.
    * **unrecognised** — set to something outside :data:`SUPPORTED_REGIONS`
      (`eu-central-1` for `eu`, say). Comparing literally would report *every*
      tenant as misaligned off one typo, which buries the real signal; the
      verdict is ``unknown`` and the reason names the misconfiguration. This is
      deliberately not a boot refusal: the value is advisory, and refusing to
      start over an advisory field trades a wrong answer for an outage.

    Pure + side-effect-free apart from a WARNING log on a genuine mismatch or a
    misconfigured value (PII-free — region tokens and the org id only). Callers
    decide what to do with the result; nothing in the request path may branch on
    it beyond reporting.
    """
    configured = resolve_region(org)
    normalized = (deployed_region or "").strip().lower()

    if not normalized:
        return ResidencyAlignment(
            status=ALIGNMENT_UNKNOWN,
            aligned=None,
            configured_region=configured,
            deployed_region=None,
            reason=REASON_DEPLOYED_REGION_UNSET,
        )

    if not is_supported_region(normalized):
        logger.warning(
            "Data-residency alignment unknown: FEOH_DEPLOYED_REGION is not one of "
            "the supported region tokens %s, so no tenant's residency can be attested",
            list(SUPPORTED_REGIONS),
        )
        return ResidencyAlignment(
            status=ALIGNMENT_UNKNOWN,
            aligned=None,
            configured_region=configured,
            deployed_region=None,
            reason=REASON_DEPLOYED_REGION_UNRECOGNISED,
        )

    if configured == normalized:
        return ResidencyAlignment(
            status=ALIGNMENT_ALIGNED,
            aligned=True,
            configured_region=configured,
            deployed_region=normalized,
        )

    logger.warning(
        "Data-residency misalignment: org %s configured for region '%s' but "
        "stack is deployed in region '%s'",
        getattr(org, "id", "?"),
        configured,
        normalized,
    )
    return ResidencyAlignment(
        status=ALIGNMENT_MISALIGNED,
        aligned=False,
        configured_region=configured,
        deployed_region=normalized,
    )
