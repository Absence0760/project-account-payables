# AI Cash-Flow Copilot

**Status: Phases 1–2 SHIPPED (read-only cash Q&A + proposed payment plans);
Phase 3 planned.** Phase 1 — the four read-only, finance-leader-gated planning
tools plus the `/api/cash-flow/copilot(+/stream)` façade and the `/cash-flow`
chat & cash-position chart — is built and live. Phase 2 — the `propose_payment_plan`
tool (`app/services/assistant/tools/cashflow.py`) + the pure plan assembler
(`app/services/cash_flow_plan.py`) + the display-only plan-card UI
(`PlanCard.svelte`) — is also built and live. Phase 3 (draft-only enactment)
below remains design-only: the enact routes (`.../draft-run`,
`.../capture-discounts`) do **not** exist yet — do not treat any Phase 3 path,
endpoint, or model named here as shipped.

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

> ⚠️ Note: the *analytics HTTP endpoints* today coerce money to `float()` for
> chart transport (`app/api/analytics.py`). The copilot tools must **not** —
> they return exact decimal strings via `model_dump(mode="json")`, matching the
> assistant's existing money discipline. Refactor `bucket_outflows` callers to
> keep `Decimal` and serialize at the edge; do not introduce float into the tool
> return path.

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

**§5 point 1 below is Phase 2 · SHIPPED** (`propose_payment_plan` tool +
`services/cash_flow_plan.py::assemble_plan`, both read-only). **Points 2–4 —
the actual enact tiers (draft run / discount capture) — are Phase 3 ·
planned**, unbuilt; nothing in the app can act on a plan yet, only propose one.

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
   both reusing existing gated paths — nothing new on the money path. **Phase 3
   — planned, not built:**
   - *Capture discounts*: flips eligible `DiscountOffer`s `offered → accepted`
     via the existing `POST /api/discounts/offers/{id}/accept`. Per today's
     design this is **status-only and never moves money** (the CFO-gated payment
     run still funds).
   - *Stage payments*: creates a **draft** `PaymentRun` via the existing payment
     -run create path — never `execute`. Execution stays behind the current
     human review + `requires_cfo_approval` gate + segregation-of-duties
     (`POST /api/payments/runs/{id}/approve` then `/execute`), unchanged.
3. **Idempotency:** plan enactment reuses the existing idempotent create paths;
   re-submitting the same proposal must not create a second draft run (dedupe on
   a `plan_id` correlation key persisted on the draft run).
4. **Confirmation UX:** the copilot presents the plan; the human clicks
   "Create draft run" / "Capture these discounts". The LLM cannot trigger either
   — the tool only *returns* the plan; the enact endpoints are ordinary
   RBAC-gated, audited, non-LLM routes.

So the LLM's influence ends at "here is a proposal"; every irreversible step is a
deterministic, human-initiated, already-audited action.

### Persistence

Reuse the tenant-scoped `assistant_conversations` / `assistant_messages` for the
chat. The **plan artifact** does not need its own table for v1 — it is a
computed, stateless object returned in the tool result and re-derivable from
inputs. If we later want "saved plans" or "plan vs. actual" tracking, add a
tenant-scoped `CashPlan` model + migration then (deferred, §12).

---

## 6. API surface

Minimal — the copilot rides the existing assistant routes; a thin façade gives
it a first-class URL and lets us set copilot-specific defaults (finance-leader
RBAC, a system-prompt hint, streaming on by default).

| Method | Path | Status | Notes |
|--------|------|--------|-------|
| POST | `/api/cash-flow/copilot` | Phase 1 · shipped | Façade over `orchestrator.run_turn`; body `{message, conversation_id?}`; RBAC `admin/ap_manager/cfo`; entity-scoped |
| POST | `/api/cash-flow/copilot/stream` | Phase 1 · shipped | SSE variant (reuses `run_turn_streaming`, identical event contract) |
| POST | `/api/cash-flow/plans/{plan_id}/draft-run` | Phase 3 · planned | Enact: create a **draft** payment run from a proposal (idempotent, audited, CFO gate at execute-time unchanged) |
| POST | `/api/cash-flow/plans/{plan_id}/capture-discounts` | Phase 3 · planned | Enact: accept the plan's discount offers (status-only, reuses discount accept) |

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
- **Proposed-plan card** — when the turn returns a plan artifact: the period-by
  -period pay schedule, captured-savings figure, and two explicit buttons
  ("Create draft run", "Capture N discounts") that call the enact endpoints. The
  buttons are gated by `auth.can(...)` / role and show the same confirm-then-act
  pattern used elsewhere.
- Money via the shared `<Money>` component (exact-string aware). Loading / empty
  / error states throughout. Build from the shared component library.

e2e (`tests-e2e/cash-flow/copilot.spec.ts`): ask a forecast question → assert the
chart + streamed answer; request a plan → assert the plan card renders → click
"Create draft run" → assert a draft (not executed) run appears and no money moved.

---

## 8. Security & invariant mapping

| Invariant | How this design satisfies it |
|-----------|------------------------------|
| Money is exact | All figures from `Decimal` pure functions; tools serialize to string, never `float` (explicitly *not* copying the analytics endpoints' float coercion) |
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
2. **Opening-balance source.** `compute_cash_position` already resolves an
   opening balance (explicit param → provider auto-sync → seed). The copilot
   should surface *which* source it used ("balance from your Modern Treasury
   account" vs "assumed $0") so the user trusts the curve. Wire the existing
   resolution's provenance field into the tool result.
3. **Multi-entity consolidation.** Phase 1 honors `X-Entity-ID` (per-entity
   view). A "show me consolidated cash across all subsidiaries" mode would ignore
   the header like `GET /analytics/by-entity` does — a small follow-up.
4. **Analytics endpoints' float coercion.** The existing forecast/what-if HTTP
   endpoints serialize money to `float` for charts. Not in scope to fix here, but
   the copilot must not inherit it, and it's worth a tracked follow-up to move
   those to exact strings too (frontend `<Money>` already handles strings).
5. **Proactive alerts.** A background sweep that pings a finance leader when a
   projected shortfall crosses a threshold (mirrors `contract_renewal` /
   `discount_auto_trigger`). Natural Phase 4; out of scope here.

---

## 13. Files this will touch (when built)

Backend: `app/services/assistant/tools/{cashflow,optimizer}.py` (new tools) +
`tools/__init__.py` (register), `app/api/cash_flow.py` (façade + enact routes),
`app/services/cash_flow_plan.py` (pure plan assembler over the existing
analytics/optimizer functions), `app/schemas/cash_flow.py`, wired into
`app/main.py`; tests `backend/tests/test_cash_flow_copilot.py`; docs — flip this
banner + add a router row to the root `CLAUDE.md` + a roadmap entry.
Frontend: `src/routes/cash-flow/+page.svelte`, `src/lib/components/cash-flow/`,
`src/lib/api/cashFlow.ts`, nav entry in `src/lib/nav.ts`, e2e
`tests-e2e/cash-flow/copilot.spec.ts`.
