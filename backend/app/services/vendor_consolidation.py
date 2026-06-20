"""Vendor consolidation / duplicate detection — pure computation.

Advisory, compute-on-read clustering of a tenant's *likely-duplicate* vendors —
the "suggest vendor consolidation" slice of Intelligent Data Enrichment. It is a
*sibling* to ``app.services.vendor_enrichment`` (the auto-fill / price-variance /
scoring stats) and reuses the fuzzy primitives from
``app.services.vendor_matching`` (``_normalize`` + ``_similarity``) rather than
reinventing them — so a name like ``"Acme Inc."`` and ``"Acme, LLC"`` normalize
to the same token bag and cluster together.

Every function here is sync + pure (no async, no IO): the caller
(``app.api.enrichment``) pulls the lightweight vendor rows + per-vendor invoice
counts from the tenant DB and hands them in already shaped, so the clustering is
unit-testable without a database and — critically — **deterministic** (no LLM, no
cloud key; local-first invariant). The slice is **advisory only**: it suggests a
canonical/primary candidate per cluster but NEVER merges or mutates any vendor.

PII: a vendor's full ``tax_id`` never leaves this module or enters a response /
log. The clustering hashes the *normalized* tax id internally to bucket and to
decide "same tax id", but only a **masked** last-4 (``***6789``) is emitted.

Performance bound (no silent O(n²) over thousands of vendors): vendors are first
partitioned into blocks by a cheap key — exact normalized ``tax_id``, exact
normalized ``code``, and the normalized name's **first token** — and the
quadratic fuzzy name comparison runs only *within* a block. Two vendors are only
compared if they already share a tax id, a code, or a leading name token, which
collapses the worst case from N² to the sum of (block size)² — tiny for a
realistic tenant where no single first-name-token bucket is huge. A hard
``MAX_VENDORS`` cap is the final backstop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.services.vendor_matching import _normalize, _similarity

__all__ = [
    "NAME_SIMILARITY_THRESHOLD",
    "MAX_VENDORS",
    "MAX_CLUSTERS",
    "VendorRecord",
    "VendorClusterMember",
    "VendorCluster",
    "mask_tax_id",
    "normalize_tax_id",
    "find_consolidation_clusters",
]

# A pair of vendors is "name-similar" enough to cluster at or above this Jaccard
# token-overlap score (the same 0..1 metric `vendor_matching.match_vendor` uses,
# where 0.6 is its match floor). We set the consolidation floor a little higher
# (0.6) to keep suggestions high-signal; an exact tax-id / code match clusters
# regardless of the name score.
NAME_SIMILARITY_THRESHOLD = 0.6

# Hard ceilings — the perf backstops described in the module docstring. Above
# MAX_VENDORS the caller skips clustering entirely (returns truncated=True);
# MAX_CLUSTERS caps the emitted clusters (strongest first).
MAX_VENDORS = 5000
MAX_CLUSTERS = 200


@dataclass(frozen=True)
class VendorRecord:
    """The lightweight projection of a vendor the caller hands in. No PII beyond
    the raw ``tax_id`` (consumed here, never emitted), which is masked on the way
    out."""

    id: str
    name: str
    code: str | None = None
    tax_id: str | None = None
    status: str | None = None
    invoice_count: int = 0
    # Lower sort key = older. The caller passes a monotonic ordinal (e.g. an
    # index over `created_at asc`) so "oldest" is decidable without dates here.
    age_rank: int = 0


@dataclass(frozen=True)
class VendorClusterMember:
    vendor_id: str
    name: str
    code: str | None
    tax_id_masked: str | None  # ***6789 — never the full tax id
    status: str | None
    invoice_count: int
    is_canonical: bool


@dataclass(frozen=True)
class VendorCluster:
    cluster_id: int
    members: list[VendorClusterMember]
    canonical_vendor_id: str
    score: Decimal  # 0..1, strongest pairwise evidence in the cluster, quantized
    reasons: list[str]  # human evidence, e.g. "same tax id", "names 0.93 similar"


@dataclass
class _Group:
    """Internal union-find accumulator for one emerging cluster."""

    ids: set[str] = field(default_factory=set)
    best_score: Decimal = Decimal("0")
    reasons: set[str] = field(default_factory=set)


def normalize_tax_id(tax_id: str | None) -> str | None:
    """Normalize a tax id for *bucketing / equality* only (never emitted).

    Strips everything but alphanumerics and upper-cases, so ``12-3456789`` and
    ``123456789`` and ``12 345 6789`` all collapse to the same key. Returns
    ``None`` for an empty / blank id (an absent tax id never clusters two
    vendors).
    """
    if tax_id is None:
        return None
    cleaned = "".join(c for c in str(tax_id) if c.isalnum()).upper()
    return cleaned or None


def mask_tax_id(tax_id: str | None) -> str | None:
    """Mask a tax id to a display-safe ``***<last4>`` (PII invariant). ``None``
    in → ``None`` out; fewer than 4 usable chars → fully masked ``***``."""
    norm = normalize_tax_id(tax_id)
    if norm is None:
        return None
    if len(norm) < 4:
        return "***"
    return f"***{norm[-4:]}"


def _norm_code(code: str | None) -> str | None:
    if code is None:
        return None
    c = str(code).strip().lower()
    return c or None


def _pair_evidence(a: VendorRecord, b: VendorRecord) -> tuple[Decimal, list[str]] | None:
    """Decide whether two vendors are likely duplicates and, if so, return
    ``(score, reasons)``. ``None`` when they're clearly distinct.

    Evidence, strongest first:
      * same normalized tax id    → score 1.0 (definitive)
      * same normalized code      → score 0.95
      * fuzzy name >= threshold   → score = the name similarity
    A tax-id / code match clusters regardless of name (a typo'd name is exactly
    the duplicate we want to catch); the name score is the fallback signal.
    """
    reasons: list[str] = []
    score = Decimal("0")

    tax_a = normalize_tax_id(a.tax_id)
    tax_b = normalize_tax_id(b.tax_id)
    if tax_a is not None and tax_a == tax_b:
        reasons.append("same tax id")
        score = max(score, Decimal("1.0"))

    code_a = _norm_code(a.code)
    code_b = _norm_code(b.code)
    if code_a is not None and code_a == code_b:
        reasons.append("same vendor code")
        score = max(score, Decimal("0.95"))

    name_sim = _similarity(_normalize(a.name), _normalize(b.name))
    if name_sim >= NAME_SIMILARITY_THRESHOLD:
        # Quantize the float similarity to 2 dp via string to stay off binary
        # float in the emitted Decimal.
        sim_dec = Decimal(f"{name_sim:.2f}")
        reasons.append(f"names {sim_dec} similar")
        score = max(score, sim_dec)

    if not reasons:
        return None
    return score, reasons


def _candidate_pairs(vendors: list[VendorRecord]) -> list[tuple[int, int]]:
    """Yield index pairs worth comparing — the perf bound. Two vendors are only
    paired if they share a block key: normalized tax id, normalized code, or the
    normalized name's first token. The quadratic comparison then runs only
    *within* each block, so the cost is Σ(block size)² instead of N²."""
    blocks: dict[str, list[int]] = {}
    for i, v in enumerate(vendors):
        keys: set[str] = set()
        tax = normalize_tax_id(v.tax_id)
        if tax is not None:
            keys.add(f"tax:{tax}")
        code = _norm_code(v.code)
        if code is not None:
            keys.add(f"code:{code}")
        norm_name = _normalize(v.name)
        first_token = norm_name.split()[0] if norm_name.split() else ""
        if first_token:
            keys.add(f"tok:{first_token}")
        for k in keys:
            blocks.setdefault(k, []).append(i)

    seen: set[tuple[int, int]] = set()
    for members in blocks.values():
        if len(members) < 2:
            continue
        for a_idx in range(len(members)):
            for b_idx in range(a_idx + 1, len(members)):
                i, j = members[a_idx], members[b_idx]
                pair = (i, j) if i < j else (j, i)
                seen.add(pair)
    return sorted(seen)


def _pick_canonical(records: list[VendorRecord]) -> str:
    """Deterministic canonical pick: most invoice volume wins; tie → oldest
    (lowest ``age_rank``); final tie → lowest id (stable)."""
    best = max(records, key=lambda r: (r.invoice_count, -r.age_rank, _neg_id(r.id)))
    return best.id


def _neg_id(vendor_id: str) -> tuple:
    """Sort key making the *lexicographically smallest* id win a max() tie."""
    return tuple(-ord(c) for c in vendor_id)


def find_consolidation_clusters(
    vendors: list[VendorRecord],
    *,
    name_threshold: float = NAME_SIMILARITY_THRESHOLD,
    max_clusters: int = MAX_CLUSTERS,
) -> tuple[list[VendorCluster], bool]:
    """Cluster likely-duplicate vendors. Returns ``(clusters, truncated)``.

    ``truncated`` is ``True`` when the tenant has more than ``MAX_VENDORS``
    vendors (clustering skipped — returns no clusters) OR when more than
    ``max_clusters`` clusters were found and the tail was dropped. The caller
    surfaces it so the UI can say "showing the strongest N".

    Clustering is transitive (union-find): if A~B and B~C, all three land in one
    cluster even if A and C alone wouldn't pass the name threshold — they're the
    same vendor seen three ways.
    """
    if len(vendors) > MAX_VENDORS:
        return [], True

    by_id: dict[str, VendorRecord] = {v.id: v for v in vendors}

    # Union-find over vendor ids.
    parent: dict[str, str] = {v.id: v.id for v in vendors}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    # Pairwise evidence, only over the bounded candidate pairs.
    pair_info: dict[tuple[str, str], tuple[Decimal, list[str]]] = {}
    for i, j in _candidate_pairs(vendors):
        a, b = vendors[i], vendors[j]
        ev = _pair_evidence(a, b)
        if ev is None:
            continue
        score, reasons = ev
        # Re-check the configurable name threshold (the default-threshold
        # _pair_evidence already applied NAME_SIMILARITY_THRESHOLD; a stricter
        # caller threshold can further filter name-only matches).
        if not _passes(a, b, score, reasons, name_threshold):
            continue
        pair_info[(a.id, b.id)] = (score, reasons)
        ra, rb = find(a.id), find(b.id)
        if ra != rb:
            parent[ra] = rb

    # Gather members per root; only roots with >= 2 members are clusters.
    groups: dict[str, _Group] = {}
    for vid in by_id:
        root = find(vid)
        groups.setdefault(root, _Group()).ids.add(vid)

    # Fold the pairwise evidence into each group.
    for (a_id, b_id), (score, reasons) in pair_info.items():
        g = groups[find(a_id)]
        if score > g.best_score:
            g.best_score = score
        g.reasons.update(reasons)

    clusters: list[VendorCluster] = []
    for g in groups.values():
        if len(g.ids) < 2:
            continue
        records = [by_id[i] for i in g.ids]
        canonical = _pick_canonical(records)
        members = [
            VendorClusterMember(
                vendor_id=r.id,
                name=r.name,
                code=r.code,
                tax_id_masked=mask_tax_id(r.tax_id),
                status=r.status,
                invoice_count=r.invoice_count,
                is_canonical=(r.id == canonical),
            )
            # Members ordered: canonical first, then by invoice volume desc, then id.
            for r in sorted(
                records,
                key=lambda r: (r.id != canonical, -r.invoice_count, r.id),
            )
        ]
        clusters.append(
            VendorCluster(
                cluster_id=0,  # assigned after sort, below
                members=members,
                canonical_vendor_id=canonical,
                score=g.best_score.quantize(Decimal("0.01")),
                reasons=sorted(g.reasons),
            )
        )

    # Strongest clusters first; tie → larger cluster, then the canonical id for
    # a stable, deterministic order.
    clusters.sort(key=lambda c: (-c.score, -len(c.members), c.canonical_vendor_id))

    truncated = len(clusters) > max_clusters
    clusters = clusters[:max_clusters]
    # Stable 1-based cluster ids after the final ordering.
    clusters = [
        VendorCluster(
            cluster_id=idx + 1,
            members=c.members,
            canonical_vendor_id=c.canonical_vendor_id,
            score=c.score,
            reasons=c.reasons,
        )
        for idx, c in enumerate(clusters)
    ]
    return clusters, truncated


def _passes(
    a: VendorRecord,
    b: VendorRecord,
    score: Decimal,
    reasons: list[str],
    name_threshold: float,
) -> bool:
    """Honour a caller-supplied name threshold stricter than the default. A
    tax-id / code match always passes (definitive); a name-only match must clear
    the configurable threshold."""
    has_hard = any(r in ("same tax id", "same vendor code") for r in reasons)
    if has_hard:
        return True
    # name-only — recompute against the configurable threshold.
    name_sim = _similarity(_normalize(a.name), _normalize(b.name))
    return name_sim >= name_threshold
