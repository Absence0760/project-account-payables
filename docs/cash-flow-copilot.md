# AI Cash-Flow Copilot

**Status: Phases 1–3 SHIPPED (read-only cash Q&A + proposed payment plans +
draft-only enactment).** Phase 1 — the four read-only, finance-leader-gated
planning tools plus the `/api/cash-flow/copilot(+/stream)` façade and the
`/cash-flow` chat & cash-position chart — is built and live. Phase 2 — the
`propose_payment_plan` tool (`app/services/assistant/tools/cashflow.py`) + the
pure plan assembler (`app/services/cash_flow_plan.py`) + the plan-card UI
(`PlanCard.svelte`) — is also built and live. Phase 3 (draft-only enactment) —
the two enact routes (`POST /api/cash-flow/plans/{plan_id}/draft-run` +
`.../capture-discounts`), the `services/payment_runs.py` shared creation
service, the `payment_runs.plan_id` idempotency anchor (migration 0079), and
the plan card's "Create draft run" / "Capture N discounts" buttons — is now
built and live too. See §5/§6/§11 below.

**Author:** platform · **Target:** beyond-parity differentiator · **Est. size:** M (3 phases)

---

## 1. Summary

A natural-language, **forward-looking, advisory** copilot that answers the two
questions a finance leader actually asks about accounts payable:

> "When am I going to run low on cash?"
> "Given my cash, what should I pay early, on time, or defer — and what does it cost me?"

It combines three primitives we already have — the **cash-flow forecast**, the
**payment-timing what-if**, and the **cash-constrained discount optimizer** —
behind the existing **conversational assistant** (so it inherits tenant
isolation, audit, budgeting, and the local-first mock/ollama/claude adapter
stack), and adds a dedicated `/cash-flow` copilot surface that can *propose* a
concrete payment plan.

**Hard boundary: the copilot never moves money.** Its most privileged write is
staging a **draft** payment run (the existing idempotent, CFO-gated path). All
funding stays behind the current human-in-the-loop payment-run execute gate.
The LLM narrates and disambiguates; it never computes a number — every dollar
figure comes from a deterministic pure function, so answers are exact and
reproducible regardless of which model (or no model) is configured.

### What it is / is not

| Is | Is not |
|----|--------|
| Advisory: forecasts, scenarios, ranked recommendations | An autonomous treasurer that pays bills |
| Deterministic money math (pure functions) | An LLM doing arithmetic on your cash |
| A new set of **read-only** planning tools + one **draft-only** staging action | A new payment rail or a bypass of the CFO gate |
| Built on the existing assistant orchestrator | A parallel chat stack |

---

## 2. Why this is a competitive edge

No mid-market competitor pairs a conversational interface with a cash-constrained
early-pay optimizer:

- **Coupa / Basware** have cash-flow analytics but no NL copilot and no
  self-serve discount optimization surfaced conversationally.
- **Tipalti / Bill / Stampli / Airbase** have dashboards and (Stampli) a
  correction-learning bot, but nothing that answers *"what should I pay this
  week given a $2M cash ceiling and which discounts are worth taking?"* in one turn.
- We already ship the hard parts — the forecast, the what-if, the ROI-ranked
  optimizer, and a tool-using assistant with true tenant isolation. This wires
  them into a single high-value experience competitors would have to build from
  scratch.

It also reinforces two existing moats: the **transparent, deterministic** design
(we show the exact math, the LLM never fabricates a figure) and **self-hostable
AI** (the copilot works fully local via the `ollama`/`mock` adapters — a
data-sovereignty story no competitor has).

---

## 3. Architecture

Build **on top of** the conversational assistant
(`backend/docs/conversational-assistant.md`), not beside it. The orchestrator
(`app/services/assistant/orchestrator.py`) already gives us, for free:

- tenant isolation via the `get_tenant` chokepoint (JWT `org`-claim cross-check),
- an append-only, PII-safe audit row per tool call (`assistant.tool_invoked`),
- a row-locked control-plane token budget + 429 refusal,
- the mock / ollama / claude adapter stack with local-first fail-soft,
- SSE streaming with the identical `tool`/`delta`/`done`/`error` contract.

```
POST /api/assistant/chat            (existing route; new tools registered)
  or POST /api/cash-flow/copilot    (thin façade — see §6)
        │  auth + tenant + entity resolved by deps
        ▼
orchestrator.run_turn / run_turn_streaming
  1. budget gate (control plane, row-locked)
  2. load/create conversation (tenant DB)
  3. adapter (mock | ollama | claude)
  4. run_tool closure ── audit row (PII-safe) ── tool fn (tenant DB, savepoint)
        │
        ├─ get_cashflow_forecast   ─┐
        ├─ run_payment_whatif       ├─ NEW read-only planning tools (§4)
        ├─ optimize_discount_capture┘   (deterministic; LLM never computes money)
        └─ propose_payment_plan     ─── NEW: assembles a plan artifact (read-only;
                                          no money moves; §5)
  5. persist messages (tenant DB)
  6. record usage (control plane)
```

### Determinism invariant (the core design decision)

The LLM's only jobs are (a) turn NL into a typed tool call and (b) narrate the
returned structured result. **Every monetary figure originates from a pure
function** we already own and unit-test:

| Copilot capability | Reuses (existing, pure) |
|---|---|
| Outflow forecast by period | `services/analytics.bucket_outflows` + `_commitment_rows` (see `app/api/analytics.py::get_cashflow_forecast`) |
| Running cash position + shortfall detection | `services/analytics.compute_cash_position` (`get_cash_position`) |
| Pay-early / on-time / late scenarios | `services/analytics.apply_payment_timing_scenario` (`get_cashflow_whatif`) |
| Which discounts to capture within a cash ceiling | `services/discount_optimizer.optimize` + `services/discount_roi.compute_roi` |

Because these are deterministic, a copilot answer is byte-reproducible under the
`mock` adapter — which is exactly what makes it testable and safe to ship
local-first.

---

## 4. New assistant tools (read-only)

Registered in `app/services/assistant/tools/` alongside the existing five, added
to `TOOLS` / `TOOL_SPECS` (`tools/__init__.py`). Each is
`async def(db, *, org_id, entity_id, current_user_id, params) -> PydanticModel`,
entity-scoped, money as **exact Decimal → JSON string** (never `float`).

> Note: the *analytics HTTP endpoints* used to coerce money to `float()` for
> chart transport (`app/api/analytics.py`). They no longer do — every money
> field there is an exact decimal string too (`backend/docs/analytics.md`
> § Money serialisation), so the copilot's discipline and the dashboard's now
> agree. The tools' own contract is unchanged: exact decimal strings via
> `model_dump(mode="json")`, never float in the tool return path.

| Tool | Params (clamped) | Returns |
|------|------------------|---------|
| `get_cashflow_forecast` | `granularity∈{day,week,month}`, `horizon_days∈[7,730]`, `include_pending` | periods `[{period, scheduled, committed, pending, discount_eligible}]` + totals |
| `get_cash_position` | + `opening_balance?`, `min_balance_threshold?` | running-balance periods + `first_shortfall_period` + flagged periods |
| `run_payment_whatif` | + `grace_days∈[0,90]` | `{early, on_time, late}` each `{total_outflow, discount_captured, weighted_avg_days_to_pay}` |
| `optimize_discount_capture` | `cash_budget?`, `cost_of_capital_pct?` | ranked recommendations `[{offer_id, vendor, apr, savings, pay_by, selected}]` + `total_savings_selected` |

**Role gating per tool.** The five cash tools (the four §4 planning tools plus
`propose_payment_plan`, §5) are finance-leader reads —
`admin` / `ap_manager` / `cfo` (mirroring analytics' `_CFO_ROLES`), **not**
`ap_clerk`. This is stricter than the assistant's blanket four-role access, so
the `run_tool` closure enforces a per-tool `allowed_roles` check and returns a
clean "not permitted" tool result (never a 500, never leaking data) when a clerk
asks a cash question. The other (existing) tools keep their current access.

**Audit shape (PII-safe).** Same as today: log the tool name + arg *shape*, never
values — e.g. `optimize_discount_capture` logs `{"has_budget": bool,
"cost_of_capital_pct": N}`, never vendor names or amounts. Cash figures live in
the answer (to an already-authenticated finance leader), never in the audit row.

---

## 5. The `propose_payment_plan` action (advisory, draft-only)

**All four points below are shipped.** Point 1 is Phase 2
(`propose_payment_plan` tool + `services/cash_flow_plan.py::assemble_plan`,
both read-only). Points 2–4 — the enact tiers (draft run / discount capture)
— are Phase 3, now built: `POST /api/cash-flow/plans/{plan_id}/draft-run` +
`.../capture-discounts` (`app/api/cash_flow.py`).

Given a cash ceiling and a horizon, `propose_payment_plan` assembles a **plan
artifact**: which open commitments to pay in which period, which discount
offers to capture, the resulting cash-position curve, and the captured-savings
total. It is built entirely from the pure functions above (`bucket_outflows`,
`compute_cash_position`, and the discount optimizer's own selection via
`run_discount_optimization` — the plan reuses `optimize_discount_capture`'s
exact selection rather than re-deriving it, so the two can never diverge). A
selected discount is re-timed onto its `pay_by` period at its discounted
outlay when it can be matched back to a single commitment row (a
vendor-scoped offer, or an invoice outside the forecast horizon, cannot be —
its offer id is surfaced in `unretimed_offer_ids` instead of silently
overclaiming precision on the curve).

**Safety model — this is the load-bearing part:**

1. **It never moves money and never mutates an invoice/payment.** It returns a
   proposal object. Full stop. **Shipped** — see above.
2. Enacting the plan is a **separate, explicit** user action with two tiers,
   both reusing existing gated paths — nothing new on the money path.
   **Shipped:**
   - *Capture discounts* (`POST /api/cash-flow/plans/{plan_id}/capture-discounts`):
     re-runs the SAME discount-optimizer pass (`run_discount_optimization`) to
     get the current `selected` offer ids, then flips each still-`offered` one
     `offered → accepted` via the existing `services.discount_offers.accept_offer`
     mutator (the same one `POST /api/discounts/offers/{id}/accept` uses).
     **Status-only and never moves money** (the CFO-gated payment run still
     funds). An offer no longer `offered` (already handled by a prior call, or
     a manual accept in the meantime) is skipped, not re-raised.
   - *Stage payments* (`POST /api/cash-flow/plans/{plan_id}/draft-run`):
     re-derives the SAME commitment rows the plan used (`_commitment_rows`),
     narrows them to invoices that are ACTUALLY payable right now
     (`PAYABLE_INVOICE_STATUSES` — a plan's horizon also includes
     pre-approval pipeline invoices, which can't be staged), and creates a
     **draft** `PaymentRun` via `services.payment_runs.create_payment_run_for_invoices`
     — the exact function `POST /api/payments/runs` uses (same payable-status
     gate, financial-integrity exception block, credit-memo netting, and
     CFO-threshold computation). Never `execute`s. Execution stays behind the
     current human review + `requires_cfo_approval` gate + segregation-of-duties
     (`POST /api/payments/runs/{id}/approve` then `/execute`), completely
     unchanged.
3. **Idempotency:** plan enactment reuses the existing idempotent create paths;
   re-submitting the same proposal must not create a second draft run. Dedupe
   is on the deterministic `plan_id` (§6) persisted on the draft run
   (`payment_runs.plan_id`, a partial-unique-indexed column — migration
   `0079_payment_run_plan_id`): retrying `.../draft-run` for the same
   `plan_id` returns the existing run (`created:false`, HTTP 200) instead of
   staging a second one. `capture-discounts` is naturally idempotent too — an
   offer no longer `offered` is simply excluded from the next optimizer pass.
4. **Confirmation UX:** the copilot presents the plan; the human clicks
   "Create draft run" / "Capture N discounts" on the plan card
   (`PlanCard.svelte`). The LLM cannot trigger either — the tool only
   *returns* the plan; the enact endpoints are ordinary RBAC-gated, audited,
   non-LLM routes, gated to the same finance-leader roles as the rest of the
   copilot.

So the LLM's influence ends at "here is a proposal"; every irreversible step is a
deterministic, human-initiated, already-audited action.

### Persistence

Reuse the tenant-scoped `assistant_conversations` / `assistant_messages` for the
chat. The **plan artifact** does not have its own table — it is a computed,
stateless object returned in the tool result and re-derivable from inputs (see
§6 for how `plan_id` substitutes for a stored primary key). If we later want
"saved plans" or "plan vs. actual" tracking, add a tenant-scoped `CashPlan`
model + migration then (deferred, §12).

---

## 6. API surface

Minimal — the copilot rides the existing assistant routes; a thin façade gives
it a first-class URL and lets us set copilot-specific defaults (finance-leader
RBAC, a system-prompt hint, streaming on by default).

| Method | Path | Status | Notes |
|--------|------|--------|-------|
| POST | `/api/cash-flow/copilot` | Phase 1 · shipped | Façade over `orchestrator.run_turn`; body `{message, conversation_id?}`; RBAC `admin/ap_manager/cfo`; entity-scoped |
| POST | `/api/cash-flow/copilot/stream` | Phase 1 · shipped | SSE variant (reuses `run_turn_streaming`, identical event contract) |
| POST | `/api/cash-flow/plans/{plan_id}/draft-run` | Phase 3 · shipped | Enact: create a **draft** payment run from a proposal (idempotent on `plan_id`, audited, CFO gate at execute-time unchanged) |
| POST | `/api/cash-flow/plans/{plan_id}/capture-discounts` | Phase 3 · shipped | Enact: accept the plan's discount offers (status-only, reuses discount accept) |

### The `plan_id` scheme (no `CashPlan` table)

Per §5's persistence note, a plan is stateless — there is nothing to look up
`plan_id` against. Instead it is a **deterministic idempotency correlation
key**: `services/cash_flow_plan.py::compute_plan_id` hashes (UUID5, not a
random `uuid4`) the plan's own RESOLVED defining inputs — `org_id`,
`entity_id`, `granularity`, `horizon_days`, `min_balance_threshold`,
`cash_budget`, `cost_of_capital_pct` — plus the calendar **date** (not a
timestamp: "today" determines which commitments are in-horizon, so a plan
computed yesterday and an identical-params plan computed today are, correctly,
two different plans).

`propose_payment_plan` computes this once and returns it as `plan_id` on the
tool result (`PaymentPlanResult`, which also echoes the resolved
`granularity` / `horizon_days` / `min_balance_threshold` / `cash_budget` /
`cost_of_capital_pct` fields verbatim). The frontend replays those same fields
in the body of both enact calls (`CashFlowPlanReplay` — see
`app/schemas/cash_flow.py`); the enact endpoints independently resolve the
identical inputs and recompute `plan_id` server-side. If it doesn't match the
`plan_id` in the URL — a tampered field, or the underlying org settings
changed since the plan was proposed, or simply a new day — the request is
refused with a clean `409` rather than silently acting on a different plan
than the one the human is looking at. **The client is never trusted for WHAT
to act on** — only its replayed parameters decide which plan_id the server
expects, and the server re-derives its own commitment rows / discount-offer
selection from scratch either way.

The plain `/api/assistant/chat` also gains the five read-only cash tools, so a
clerk-free finance user can ask cash questions in the general assistant too
(subject to the per-tool role gate). The façade exists mainly for the dedicated
UI and defaults.

---

## 7. Frontend

A `/cash-flow` route (Insights nav group), Svelte 5 runes, static — no SSR,
fetches via `$lib/api.ts`:

- **Copilot panel** — chat over `/api/cash-flow/copilot/stream`, reusing the
  assistant's streaming client + the chartable structured `result` (the forecast
  / position / optimizer results render as the existing chart components; the
  prose streams alongside).
- **Cash-position chart** — running-balance curve with shortfall periods flagged
  red (from `get_cash_position`).
- **Proposed-plan card** (`PlanCard.svelte`) — the period-by-period pay
  schedule, captured-savings figure, and two explicit buttons ("Create draft
  run", "Capture N discounts") that call `$lib/api/cashFlow.ts`
  (`createDraftRunFromPlan` / `captureDiscountsFromPlan`, both over
  `$lib/api.ts`, never raw `fetch`). The buttons are gated by role
  (`auth.isManager || auth.isCfo` — mirroring the backend's `_COPILOT_ROLES`,
  the same check `/discounts`' accept button uses) and rendered only when the
  plan has anything to act on. "Create draft run" fires on a single click
  (mirrors the manual payments-queue "Create draft run" button — a draft
  isn't destructive); "Capture N discounts" uses an armed two-click confirm
  (mutates several `DiscountOffer` rows). A `409` from either call — the
  stale-plan guard (§6) — surfaces a friendly "ask the copilot for a fresh
  plan" notice instead of a raw error (`ApiError`, `$lib/api.ts`, carries the
  HTTP status so the frontend can branch on it without parsing message text).
- Money via the shared `<Money>` component (exact-string aware). Loading / empty
  / error states throughout. Build from the shared component library.

No dedicated Playwright e2e spec for the enact buttons yet (backend coverage
is in `backend/tests/test_cash_flow_copilot.py` §10 below) — a natural
follow-up once the surface has real usage to script against.

---

## 8. Security & invariant mapping

| Invariant | How this design satisfies it |
|-----------|------------------------------|
| Money is exact | All figures from `Decimal` pure functions; tools serialize to string, never `float`. The analytics HTTP endpoints have since been migrated to the same exact-string contract, so there is no longer a float coercion to avoid copying |
| Idempotency on money-moving writes | Copilot moves no money; the only write is a draft run via the existing idempotent create path, deduped on `plan_id` |
| Audit trail append-only | Every tool call audited (existing orchestrator); every enact action audited via the existing payment-run / discount-accept audit rows |
| Tenant isolation at the data layer | Inherited from `get_tenant` chokepoint + entity scoping; tools bound to one tenant session |
| Auth before everything + RBAC | Façade + tools gated `admin/ap_manager/cfo`; per-tool `allowed_roles` enforced in `run_tool`; clerk gets a clean refusal |
| PII out of logs | Audit logs arg *shape* only; cash figures never enter the trail |
| Secrets via sops | No new secret — reuses `FEOH_ANTHROPIC_API_KEY`; local-first via mock/ollama |
| Local-first | Fully functional under `mock` (deterministic) and `ollama` (default); no cloud account required |

The critical new security surface is the **per-tool role gate** (clerks must not
read org cash) and the **draft-only boundary** on plan enactment. Both get
explicit tests (§10).

---

## 9. Configuration (`FEOH_` prefix)

Reuses the assistant's config; a couple of additive knobs:

| Variable | Default | Purpose |
|----------|---------|---------|
| `FEOH_CASHFLOW_COPILOT_ENABLED` | `true` | Master switch for the copilot tools + façade routes |
| `FEOH_CASHFLOW_COPILOT_DEFAULT_HORIZON_DAYS` | `90` | Default forecast horizon when the user doesn't specify |
| `FEOH_CASHFLOW_SHORTFALL_ALERTS_ENABLED` | `false` | Master switch for the projected-shortfall alert sweep (§14). OFF by default so local dev / tests never email a CFO |
| `FEOH_CASHFLOW_SHORTFALL_ALERTS_INTERVAL_SECONDS` | `86400` | Sweep tick. Daily — a cash forecast doesn't move hour to hour |
| `FEOH_CASHFLOW_SHORTFALL_ALERTS_HORIZON_DAYS` | `90` | How far ahead the alerting forecast looks. Separate from the copilot's interactive default so an operator can alert on a shorter, more actionable window |
| (reused) `FEOH_ASSISTANT_PROVIDER` / `FEOH_ASSISTANT_MONTHLY_TOKEN_BUDGET` / `FEOH_ANTHROPIC_API_KEY` | — | Adapter, budget, key — **no new secret** |
| (reused) `FEOH_DISCOUNT_COST_OF_CAPITAL_PCT` | `8.0` | Optimizer cost-of-capital when the user doesn't override |

---

## 10. Testing

- **Determinism / money-exactness** — under the `mock` adapter, a fixed seeded
  tenant produces a byte-stable forecast/plan; assert money is exact-string, no
  float anywhere in the tool return path.
- **Optimizer correctness** — reuse/extend the existing `optimize` unit tests;
  assert the copilot tool returns the same selection the `/api/discounts/optimize`
  endpoint does for the same inputs (single source of truth).
- **Draft-only boundary (critical)** — **Phase 2, shipped:** `propose_payment_plan`
  mutates nothing at all — assert no `Payment`/`PaymentRun` is created and no
  invoice status changes (`test_propose_payment_plan_never_mutates_anything`).
  **Phase 3, planned:** once the enact routes exist, assert they create a
  `draft` run and **execute nothing** — no `Payment` leaves `draft`, no invoice
  transitions to `paid`, and the CFO gate still guards execute.
- **Per-tool RBAC** — an `ap_clerk` asking a cash question gets a clean refusal
  tool result (not data, not a 500); `test_rbac.py` coverage gate stays green for
  the new routes.
- **Tenant isolation** — tenant B's copilot never sees tenant A's commitments
  (inherited, but assert it).
- **Idempotency** — enacting the same `plan_id` twice yields one draft run.
- **Streaming parity** — the SSE `tool`/`delta`/`done` contract is unchanged;
  the plan artifact rides a `tool` frame before the prose.

---

## 11. Phasing

1. **Phase 1 — Read-only cash Q&A. ✅ SHIPPED.** The four planning tools + per-tool
   RBAC + the `/api/cash-flow/copilot(+/stream)` façade + the `/cash-flow` chat &
   cash-position chart. Ships value with zero write surface.
2. **Phase 2 — Proposed plans. ✅ SHIPPED.** `propose_payment_plan` tool + the
   plan card UI (display only, no enact). The optimizer + cash-position math
   drive a concrete, re-timed pay schedule; nothing moves money.
3. **Phase 3 — Draft-only enactment. (planned)** The two enact endpoints (draft
   run + discount capture), idempotent + audited, human-confirmed, CFO gate
   unchanged.

Each phase is independently shippable and independently valuable. As of Phase 2,
the enact routes in §6 (`.../draft-run`, `.../capture-discounts`) are still
design-only — everything else on this page is shipped.

---

## 12. Deferred / open questions

1. **Saved plans + plan-vs-actual.** A tenant-scoped `CashPlan` model + migration
   to persist a proposal and later compare it to what actually got paid. Deferred
   until there's demand; v1 plans are stateless/re-derivable.
2. ~~**Opening-balance source.**~~ **SHIPPED.** The resolution chain lives in
   `services/cashflow.py::resolve_opening_balance` — **one owner, and all three
   consumers go through it**: the copilot tools, the §14 alert sweep, and
   `GET /api/analytics/cash_position` (the `/cfo` dashboard's chart, which had
   its own duplicate inline copy). A copilot answer, an alert email, and the
   dashboard therefore cannot start from a different number. It returns an
   `OpeningBalance` carrying its own provenance: `source`
   (`explicit` | `provider` | `settings` | `none`), plus the `provider` name and
   the adapter's opaque `account_ref` when a bank sync supplied it — so the
   copilot can say "from your Modern Treasury operating account" rather than
   quoting an unattributable number. `CashPositionResult` and
   `PaymentPlanResult` surface all of it (`opening_balance_source` /
   `_provider` / `_account_ref`), and `CashPositionChart` / `PlanCard` already
   render the source label in every locale.

   The same change closed a real defect it exposed: the provider link used to
   take the adapter's amount and **drop its currency**. Every outflow subtracted
   from the opening balance is denominated in the org's reporting currency, so
   an org reporting in USD with a EUR operating account got a running balance
   that was silently a mixture of two currencies — and the plan proposals and
   shortfall figures priced off it were wrong by the exchange rate, with no
   signal. A provider balance in any other currency is now **refused**: the
   chain falls through to the persisted/zero link and flags
   `opening_balance_provider_skipped: "currency_mismatch"`, so the fallback
   can't be mistaken for "no bank is connected". Converting was rejected —
   an FX rate fetched on a read makes the curve non-deterministic and
   unreproducible (§3, `decisions.md` §18), and the fix an operator needs is to
   set a reporting currency or a BYO opening balance, not for us to guess. A
   blank / unknown provider currency fails closed the same way.

   **The outflow side had the mirror-image defect and is now fixed too.**
   `analytics._commitment_rows` selected `Invoice.amount` — each invoice's OWN
   currency — with no conversion, so the premise the paragraph above rests on
   ("every outflow subtracted from the opening balance is denominated in the
   org's reporting currency") was true of the balance and false of the
   outflows. A ¥10,000,000 invoice subtracted from a $250,000 opening balance
   projected a −$9.75M shortfall that did not exist: the alert sweep emails
   every finance leader about it, `propose_payment_plan` re-times payments
   around it, and `POST /plans/{id}/draft-run` stages a payment run off that
   plan. A GBP invoice under-counts in the same way and can hide a REAL
   shortfall. Every row now goes through `currency_conversion.reporting_amount_for_row`
   — the same helper every sibling rollup in `api/analytics.py` uses, reading
   the rate locked on the invoice, never fetching one on a read — and carries
   an `unconverted` flag so a row we could not convert is visible rather than
   silently mixed in.

   **That flag is now actually read.** For a while it was computed on every
   row and consumed by nobody, so a foreign invoice with no usable lock still
   entered the curve at face value with nothing on any surface to contradict
   it — the same −$9.75M, just arrived at one rung later.
   `analytics.bucket_outflows` counts it per period, `compute_cash_position`
   carries it through the running balance, and it surfaces as
   `unconverted_count` on `/api/analytics/{cashflow_forecast,cashflow_whatif,
   cash_position}` and on the `get_cashflow_forecast` / `get_cash_position` /
   `run_payment_whatif` tool results. It is the OUTFLOW-side twin of
   `opening_balance_provider_skipped` above: non-zero means the totals mix
   currencies, and — because the closing balance carries forward — one such row
   poisons every later period.

   `GET /api/analytics/cash_position` now returns the same provenance
   (`opening_balance_source` / `_currency` / `_provider` /
   `_provider_skipped`). Its explicit-override source value changed from
   `"query"` to `"explicit"` — the two names always meant the same thing, and
   the `cashFlow.chart.source.explicit` i18n key already existed in every
   locale while `…source.query` never did.

   **The `/cfo` cash-position card renders the refusal.** It used to show only
   the `source === 'none'` prompt, so "we have your bank balance and declined to
   use it" and "no bank is connected" looked identical on the surface where the
   number is actually read — the copilot's chat narration was the only place a
   human saw the difference. The card now renders a distinct amber notice
   whenever `opening_balance_provider_skipped` is set (independent of `source`,
   since the refusal can fall through to a persisted figure as easily as to
   zero), naming the reporting currency and what to do about it. The reason
   code → message mapping is the pure `routes/cfo/openingBalanceNotice.ts`; an
   **unrecognised** code deliberately falls back to a generic line rather than
   to silence, because the backend can add a reason before the frontend learns
   its wording and silence is the failure mode this closes. Display only — no
   backend change; the API already carried the field.
3. **Multi-entity consolidation.** Phase 1 honors `X-Entity-ID` (per-entity
   view). A "show me consolidated cash across all subsidiaries" mode would ignore
   the header like `GET /analytics/by-entity` does — a small follow-up. (The
   §14 alert sweep already runs org-wide for exactly this reason; the
   interactive tools still honour the header.)
4. ~~**Analytics endpoints' float coercion.**~~ **SHIPPED** — every money field
   on `/api/analytics/*` is now an exact decimal string, with the frontend and
   Flutter consumers moved across in the same change. Day counts, percentages
   and counts stay JSON numbers. See `backend/docs/analytics.md`
   § Money serialisation.
5. ~~**Proactive alerts.**~~ **SHIPPED** — see §14.

---

## 14. Proactive projected-shortfall alerts (shipped)

`app/services/cash_flow_alerts.py` — a background sweep that pushes the cash
forecast instead of waiting for someone to pull it. The cash-position curve and
its pure breach detector (`services/analytics.detect_threshold_breaches`) both
predate this; until now their only consumer was a dashboard read, so a finance
leader learned about a projected shortfall by going looking for it.

Mirrors `contract_renewal` / `discount_auto_trigger` exactly: a long-lived
asyncio task started in `main.lifespan`, one fresh engine per tenant, one org's
failure logged (exception *class* only — PII-out-of-logs) and skipped rather
than halting the sweep, **off by default** behind
`FEOH_CASHFLOW_SHORTFALL_ALERTS_ENABLED`.

Per org, per tick:

1. **Opt-in check.** Skip unless the org has a persisted
   `settings.cashflow.min_balance_threshold` (managed by
   `GET/PUT /api/analytics/cash-position-settings`). The threshold *is* the
   opt-in — with no line there is nothing to breach and nothing to say.
2. Build the org-wide commitment rows via the same `_commitment_rows` the CFO
   dashboard and the copilot use, bucket them weekly over
   `FEOH_CASHFLOW_SHORTFALL_ALERTS_HORIZON_DAYS`, resolve the opening balance
   through the shared `resolve_opening_balance` (§12.2), and run
   `compute_cash_position` → `detect_threshold_breaches`.
3. `project_shortfall` (pure) reduces the breach list to the **earliest**
   breaching period — the deadline the finance leader is actually working
   against — carrying the count of all breaching periods so the message can say
   how widespread it is.
4. If that period differs from the one the org was last alerted about, notify
   its finance leaders once (`notification_dispatch.notify_event`, in-app +
   email, per-user-preference gated) and record the period on
   `Organization.settings.cashflow.shortfall_alert`.

| Property | How |
|---|---|
| **Never moves money** | No `Payment` / `PaymentRun`, no discount accepted, no invoice touched. The only write beyond the notification rows is the alerted-period marker in the org's settings JSON (no migration) |
| **Idempotent** | The marker is the dedupe — the role `renewal_alert_sent_at` plays in `contract_renewal`. A standing shortfall is announced once per projected period; the marker clears when the projection clears, so a recurrence is announced again |
| **Fails in the safe direction** | The notification is sent *before* the marker is written, so a crash between the two re-alerts next tick rather than swallowing the warning. For an alert, a duplicate is recoverable and a miss is not |
| **One answer on audience** | `ALERT_ROLES` is pinned by a drift-guard test to `api/cash_flow.py::COPILOT_ROLES` (`admin` / `ap_manager` / `cfo` — not `ap_clerk`), so the push surface and the pull surface can't disagree about who may see org cash. An org with no finance leaders yet leaves the marker unwritten so a later sweep still fires |
| **Org-wide by design** | `entity_id=None` — a treasury shortfall is a question about the whole legal group's cash, the same consolidated posture `GET /analytics/by-entity` takes by ignoring `X-Entity-ID` |
| **PII-free** | The message carries org-level aggregates only (projected closing balance, threshold, shortfall), formatted straight off the `Decimal`; log lines carry counts and exception classes, never a cash figure |

New notification event: `cash_shortfall_projected` (`entity_type:
"cash_position"`, `entity_id: NULL` — the alert is about the org's whole
projected position, not any one record). Rendered by
`notification_templates.render_cash_shortfall`, pre-rendered and handed to
`notify_event(rendered=…)` like `contract_renewal_due`.

Tests: `backend/tests/test_cash_flow_alerts.py` (pure reduction, mocked
multi-org fan-out + failure isolation + dedupe + re-arm + recipient-less retry,
and a real-Postgres pass proving the breach comes from real invoices and that
nothing on the money path moves).

---

## 13. Files this will touch (when built)

Backend: `app/services/assistant/tools/{cashflow,optimizer}.py` (new tools) +
`tools/__init__.py` (register), `app/api/cash_flow.py` (façade + enact routes),
`app/services/cash_flow_plan.py` (pure plan assembler over the existing
analytics/optimizer functions), `app/services/cashflow.py` (opening-balance
resolution + provenance, §12.2), `app/services/cash_flow_alerts.py` (the §14
shortfall sweep, wired into `app/main.py`'s lifespan),
`app/schemas/cash_flow.py`, wired into `app/main.py`; tests
`backend/tests/test_cash_flow_copilot.py`,
`backend/tests/test_cash_flow_opening_balance.py`,
`backend/tests/test_cash_flow_alerts.py`; docs — flip this banner + add a
router row to the root `CLAUDE.md` + a roadmap entry.
Frontend: `src/routes/cash-flow/+page.svelte`, `src/lib/components/cash-flow/`,
`src/lib/api/cashFlow.ts`, nav entry in `src/lib/nav.ts`, e2e
`tests-e2e/cash-flow/copilot.spec.ts`.
