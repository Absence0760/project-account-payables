# Adaptive AI Workflows

> **Advisory only.** Nothing in this feature approves an invoice, adjusts a
> threshold, assigns an approver, or mutates a workflow definition. It is a set
> of **read models** over approval history plus an **advisory** suggestion store.
> Smart routing **recommends** approvers but never assigns one; the remaining
> "act" surfaces (auto-adjusting thresholds, A/B testing, retraining, and the
> apply path that actually assigns the routed approver) are tracked follow-ups,
> not built here.

> **Local-first / deterministic.** All learning and anomaly detection is plain
> statistics (`Decimal`) over the tenant's own data. There is **no LLM call** and
> nothing requires a cloud key — the whole feature runs on a laptop with `pnpm
> dev`. If a future slice adds LLM phrasing for a suggestion, it MUST fail soft to
> the template with no key, mirroring `services/audit_summary.py`.

## Overview

Four surfaces, all under `/api/adaptive`:

1. **Approval-pattern learning** (`GET /approval-patterns`) — per-approver and
   per-vendor aggregates over the tenant's approval/rejection history.
2. **Anomaly detection** (`GET /anomalies`) — flag an invoice (or batch of
   in-review invoices) against the learned per-vendor baseline; returns the
   baseline it compared against.
3. **Workflow-change suggestions** (`GET /suggestions`,
   `POST /suggestions/{id}/dismiss`) — derived "consider auto-approve under $X"
   suggestions, persisted with `open / dismissed / applied / stale` status.
4. **Smart routing** (`GET /routing-suggestion`) — for one invoice, **rank** the
   org's eligible approvers by routing fit (fastest + most-consistent + most
   vendor-familiar), purely from their approval history. Advisory — never
   assigns anyone.

The statistics live in `app/services/adaptive_workflows.py` (pure, sync, no IO —
unit-testable without a DB). The SQL, the control-plane name join, response
shaping, and suggestion persistence live in `app/api/adaptive_workflows.py`.

## Relationship to the existing fraud / anomaly code

This feature does **not** duplicate `services/invoice_warnings.py`. They are
complementary:

| | `invoice_warnings.fraud_stat_anomaly` | `/api/adaptive/anomalies` |
|---|---|---|
| Trigger | Automatic, on every extraction / mutation (`refresh_warnings`) | On-demand read (GET) |
| Scope | Single vendor, amount only (`amount > mean + N·σ`) | Per-vendor baseline, **three** axes (amount / approver / timing) |
| Side effects | **Writes** warnings + Exception rows | **None** — read-only; returns the baseline for explainability |
| Audience | The invoice processing pipeline | A manager/CFO investigation surface |

If you're reading one and wondering whether the other is dead code: it isn't.
The adaptive endpoint is the on-demand, explainable view; the warning is the
inline pipeline flag. The adaptive code never calls `refresh_warnings` and never
creates Exceptions.

Similarly, `services/analytics.py` has `compute_processing_time_metrics` /
`compute_approval_bottleneck` (a per-stage operational cut). The adaptive
learning service is a per-approver / per-vendor **behaviour** cut and reuses
`analytics._avg` / `_quantile` / `_decimal_days` by importing them.

## Data sources

All reads are tenant-DB (via `get_tenant_db`). Optional entity scoping: when
`X-Entity-ID` resolves to a non-None UUID (`get_entity_id`), invoices are
filtered to that entity; `None` (or absent / `all`) is the consolidated view.

- **Approval / rejection events** come from `audit_log` joined to `invoices`
  (`audit_log.entity_id = invoices.id`):
  - `action = "invoice.approved"` → an approval. `details->'changes'` present ⇒
    approved-**with-corrections**; absent ⇒ approved **unmodified**.
  - `action = "invoice.rejected"` → a rejection.
  - `actor_id` is the approver; `created_at` the decision time.
- **Time-to-approve clock start** = the invoice's transition **into**
  `ready_for_review` — the earliest `audit_log` row for that invoice whose
  `details->>'new_status' = 'ready_for_review'`. Fallback when none exists
  (e.g. auto-approved straight from `new`): `invoices.created_at`. The
  clock-start lookup is bounded to the same `since` window as the decision
  rows, so an out-of-window `ready_for_review` transition can't be paired with
  an in-window approval. Elapsed days via `analytics._decimal_days` (simple
  elapsed days, not business-day weighted — a deliberate follow-up), **clamped
  at ≥ 0** so an out-of-order / backfilled audit row can't feed a negative
  day-count into the median or baseline.
- **Vendor baseline** (for anomaly detection) is built from that vendor's
  **historically-approved** invoices only — `status IN (approved, sending_to_erp,
  sent_to_erp, posted_in_erp, payment_scheduled, paid, done)`. Pending / rejected
  invoices are not part of the "accepted norm". The approver + timing per
  baseline invoice come from its `invoice.approved` audit row.

Approver display names are joined from the **control-plane** `User.full_name` in
a separate query (tenant and control are different databases — no cross-DB join).

## Statistics

All amount math is `Decimal`; amounts quantize to `0.01`, rates/days to `0.1`.

- `approval_rate_pct = approved / (approved + rejected) * 100` (0 when no
  decisions).
- `consistency_pct = unmodified / approved * 100` (0 when no approvals) —
  share of approvals made with no field corrections.
- `avg / median / min / max approved_amount` over the approved-amount list.
- `median / avg time_to_approve_days` over the non-None per-invoice legs.
- `_stdev` — population standard deviation, `Decimal` (`Decimal.sqrt`); `<2`
  samples → 0. (analytics doesn't expose one, so it's defined here.)

### Anomaly rules

Given a per-vendor `VendorBaseline` (built only when `>= min_history` approved
invoices; otherwise `insufficient_history=True`, no flags):

1. **`amount_high`** (warning) — `amount > mean + sigma·stdev` **AND**
   `amount > median · median_multiple`. The AND-guard means a tight-variance
   vendor (small σ) doesn't trip on a modest absolute jump, while a wide-variance
   vendor still flags genuine outliers.
2. **`amount_low`** (info) — `amount < mean − sigma·stdev`, `amount > 0`, `σ > 0`
   (unusually tiny — possible test transaction / split invoice).
3. **`unusual_approver`** (info) — only when an approver is supplied (the GET
   endpoint passes the invoice's `assigned_to_id` if set) and that approver is
   not among the vendor's historical `typical_approver_ids`.
4. **`off_pattern_timing`** (info) — an in-flight invoice's time-in-review
   exceeds `median_time_to_approve_days · timing_multiple`.

The result always carries the `baseline` it compared against (explainability).

### Suggestion derivation

`auto_approve_threshold` is the only kind this slice. A vendor qualifies when:
`approved_count >= suggestion_min_history` **AND** `rejected_count == 0` **AND**
**zero modifications** (`unmodified_count == approved_count`). The
zero-modifications gate is **absolute** — it deliberately mirrors the absolute
`rejected_count == 0` gate rather than the older percentage floor, because the
suggestion's own copy asserts a spotless `{n}/{n} approved unmodified` record:
a single corrected approval would make that claim false, so a single corrected
approval disqualifies the vendor. `suggestion_min_consistency_pct` is retained
as an additional (weaker) floor for org-config symmetry, but the absolute gate
is what governs. The suggested threshold is the vendor's max approved amount
rounded **up** to the nearest $500. `confidence_pct = min(consistency_pct, 99)`
(never claims 100%). Messages are deterministic template strings — no LLM.

### Smart routing

`recommend_approvers(eligible, approver_patterns, ...)` (pure, in
`services/adaptive_workflows.py`) ranks the org's **eligible approvers** for one
invoice. Eligible = active control-plane `User`s in the org holding an
approval-capable role (`admin` / `ap_manager` / `cfo`; `ap_clerk` enters
invoices but doesn't approve, so it's excluded). Each candidate gets a
deterministic 0–100 score = weighted sum of four sub-scores (each normalised to
0..1):

| Sub-score | Weight | Formula |
|---|---|---|
| **speed** | 45 | `1 − median_time_to_approve_days / horizon` (clamped 0..1); a 0-day median for a real approver = 1 (fastest); no approval history = 0 |
| **consistency** | 25 | `approval_rate_pct / 100` (fewer rejections / rework) |
| **vendor familiarity** | 20 | `min(vendor_approved_count, 5) / 5` — how many of *this vendor's* invoices the approver has approved before |
| **experience** | 10 | `min(sample_size, 20) / 20` — total decisions made (more signal) |

`horizon` defaults to 14 days. Vendor familiarity is computed in the API layer
by counting this approver's approvals of the invoice's vendor over the same
decision-row window the patterns are built from. The per-approver speed /
consistency / experience come from `compute_approver_patterns` (all-vendor
history).

Ties break on more vendor familiarity → larger sample → `approver_id` (stable +
deterministic). An eligible approver with **no** decision history still appears
(so a new-but-valid approver is routable) but scores only on familiarity.
`insufficient_history` is True only when **no** eligible approver has any history
to rank on — the caller should then fall back to its normal assignment policy.
Each candidate carries a `reasons` list (deterministic human-readable strings)
explaining its score. **The result never assigns anyone** — it's a ranked read
model; the apply path that would actually set `assigned_to_id` routes through the
future audited assignment slice.

## Tunables — `Organization.settings.adaptive`

Partial override (omit a key to inherit; unknown keys dropped — mirrors
`fraud_rules`):

| Key | Default | Used by |
|---|---|---|
| `sigma` | `2.0` | amount_high / amount_low |
| `median_multiple` | `3.0` | amount_high guard |
| `timing_multiple` | `3.0` | off_pattern_timing |
| `min_history` | `5` | baseline minimum (anomaly) |
| `suggestion_min_history` | `12` | suggestion minimum approvals |
| `suggestion_min_consistency_pct` | `95` | suggestion consistency gate |

## Endpoints

| Route | Method | RBAC | Notes |
|---|---|---|---|
| `/api/adaptive/approval-patterns` | GET | admin / ap_manager / cfo | `?days=180` (max 730) lookback |
| `/api/adaptive/anomalies` | GET | admin / ap_manager / cfo | `?invoice_id=<uuid>` (single) or batch (in-review queue, cap 200); 404 if the invoice isn't in this tenant/entity |
| `/api/adaptive/suggestions` | GET | admin / ap_manager / cfo | `?status=open|all` (default `open`), `?days=365`; **upserts** then returns |
| `/api/adaptive/suggestions/{id}/dismiss` | POST | admin / ap_manager | idempotent; 404 outside tenant |
| `/api/adaptive/routing-suggestion` | GET | admin / ap_manager / cfo | `?invoice_id=<uuid>` (required), `?days=180` lookback; 404 if the invoice isn't in this tenant/entity. Ranked, advisory — assigns nobody |

Read routes exclude `ap_clerk` — this is a manager/CFO surface, matching the
analytics precedent. Every route is behind `get_current_user` + `require_roles`.

## Persistence — `workflow_suggestions`

Tenant-scoped table (migration `0031_workflow_suggestions`, fans out to every
tenant DB; fresh tenants get it from `create_all` via the registered model).
Money in `payload` is **string-Decimal**; `confidence_pct` is `Numeric(5,2)` —
never a float.

The table is a **materialized cache**, not the source of truth for the
statistics:

- `GET /suggestions` recomputes `derive_suggestions` every call and **upserts**
  by `dedupe_key` (`INSERT ... ON CONFLICT (dedupe_key) DO UPDATE`): a new
  qualifying vendor inserts as `open`; an existing row's `payload / title /
  rationale / confidence` are refreshed to the latest stats but its **status is
  never overridden** (a `stale` row that holds again re-opens; `dismissed` /
  `applied` are left alone).
- An `open` suggestion whose condition **no longer holds** flips to `stale`
  (not deleted) so the history of what was once suggested survives.

**Why persist** (vs compute-on-read): a dismissal must be **durable** across
requests even as the underlying stats shift, and `status='applied'` is the hook
for the future apply-path slice. Compute-on-read can't remember a dismissal.

**Write-on-GET.** `GET /suggestions` performs the upsert. This is an idempotent
materialization — no money moves and it is not an auditable status change — so
it's acceptable for the single-round-trip ergonomics the UI wants. The noted
alternative, if a reviewer objects, is a `POST /suggestions/refresh` that does
the upsert plus a pure read-only `GET`.

**No audit row on dismiss.** Dismissing a suggestion mutates only the advisory
row's status — not an invoice, payment, approval, or vendor — so the
append-only-audit invariant (which is about those regulated status changes) does
not apply.

## Deferred follow-ups

- **Smart routing — apply path.** `GET /routing-suggestion` recommends approvers
  (shipped); the **apply** path that actually sets `Invoice.assigned_to_id` to a
  routed approver is not built. When built it MUST route through the audited
  `review.assign_reviewer` so an assignment writes an audit row, exactly like the
  threshold apply path below.
- **Auto-adjusting thresholds** — when built, the **apply** path MUST route
  through the audited `review.approve_invoice` / workflow-definition PATCH so
  audit rows are written. `status='applied'` is the placeholder for this.
- **A/B testing** of workflow rules.
- **Model-retraining feedback loop.**
- **LLM phrasing** of suggestion text — if added, must fail soft to the template
  with no key (mirror `services/audit_summary.py`).
- Business-day-weighted time-to-approve (currently simple elapsed days).
- No frontend/mobile UI yet.
