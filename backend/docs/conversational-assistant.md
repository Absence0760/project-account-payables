# Conversational AP Assistant

A per-user, per-tenant natural-language assistant over a **fixed, typed
toolset** — five read-only tools that run only against the caller's current
tenant. The assistant never exposes raw SQL and never reads another tenant's
data; the model can only emit one of the five fixed tool calls with typed,
clamped parameters.

**Local-first.** The default `mock` adapter routes a query to one tool via
deterministic keyword/intent heuristics — no network, no key. A real `claude`
adapter (Anthropic Messages API tool-use) is selected only when an API key is
configured; the dispatcher auto-downgrades `claude` → `mock` when
`AP_ANTHROPIC_API_KEY` is empty, so `pnpm dev` runs with no credential.

## Architecture

```
POST /api/assistant/chat
        │  (auth + tenant resolved by deps; employee roles only)
        ▼
orchestrator.run_turn
  1. budget gate (control plane, row-locked)  → 429 on exceed
  2. load/create conversation (org,user)       → tenant DB
  3. build adapter (mock | claude)
  4. run_tool closure  ── audit row (PII-safe) ── tool fn (tenant DB, savepoint)
  5. persist user + assistant messages (tenant DB)
  6. record usage (control plane upsert; no standalone commit)
```

Persist (5) runs **before** record (6) and neither commits on its own — both
ride the request's commit boundary (`get_tenant_db` / `get_control_db` exit). So
a turn that unwinds after the tool work rolls back the conversation rows, the
audit rows, **and** the token debit together: usage is never charged for a turn
whose conversation/audit rows never landed.

Tenant isolation **and** audit logging live in the orchestrator's `run_tool`
closure — not in the adapters. Both adapters call `run_tool`; neither touches
the DB or the audit infra directly. A leaked/spoofed `X-Tenant-Slug` can't widen
access because `get_tenant` already cross-checks the JWT `org` claim, and every
tool is bound to that one tenant session.

### Adapter registry (`app/services/assistant/`)

Mirrors the extraction-adapter registry (`@register_assistant_adapter` +
`get_assistant_adapter`). Config is assembled from settings:

```python
{"provider": settings.assistant_provider,             # "mock" | "claude"
 "api_key":  settings.anthropic_api_key,              # reused — no new secret
 "model":    settings.assistant_model or settings.extraction_model}
```

- **`mock_adapter.py`** (default) — ordered keyword/intent rules route the
  message to one tool, run it, and format a deterministic templated answer.
  Token counts are a deterministic estimate (`len//4`) so the meter + budget
  path are exercised identically to the claude path.
- **`claude_adapter.py`** — raw `httpx` POST to
  `https://api.anthropic.com/v1/messages` (house style; matches
  `extraction_adapters/claude_vision.py`), `thinking: {"type": "adaptive"}`,
  the five tools as Anthropic tool schemas, and a manual tool-use loop capped at
  `AP_ASSISTANT_MAX_TOOL_HOPS`. The model id resolves from config
  (`AP_ASSISTANT_MODEL` → falls back to `AP_EXTRACTION_MODEL`) — never
  hardcoded. Real `usage` tokens are summed across hops.

## The five tools

Each is an `async def(db, *, org_id, entity_id, current_user_id, params)` over
the **current tenant** session, returning a Pydantic model. Money is `Decimal`
end to end; transport uses `model_dump(mode="json")` (Decimal → string, never
float). Params clamp limits so an odd model arg can't request an unbounded scan.

| Tool | Wraps | Returns |
|------|-------|---------|
| `list_invoices` | filtered `select(Invoice)` (entity-scoped) | invoice summaries + total + applied filters |
| `get_vendor_spend` | `services.analytics.compute_supplier_concentration` (statuses from `app/api/analytics._COMMITTED_STATUSES`) | top-N vendors + share %, reporting currency |
| `list_pending_approvals` | `ready_for_review` invoices ⋈ active approval `WorkflowStep` | approval-queue rows (`assignee=me\|anyone`) |
| `get_payment_forecast` | `services.analytics.bucket_outflows` (`_COMMITTED_STATUSES` + `_PENDING_STATUSES`) | due-dated outflow buckets + total |
| `find_invoices_by_text` | `services.rag.retrieve_similar` (pgvector; mock embeddings by default) | similar invoices + non-PII snippet |

`list_invoices` re-builds the filter SELECT directly rather than importing from
`app/api/invoices.py` (frozen during in-flight multi-entity work) — see
[Deferred](#deferred). All tools narrow reads to one subsidiary when an
`X-Entity-ID` is selected: the four SQL tools via `apply_entity_scope`, and
`find_invoices_by_text` by threading `entity_id` into `retrieve_similar`, which
joins each embedding back to its `Invoice.entity_id` (the embedding row carries
no `entity_id` of its own). With no entity selected (`entity_id=None`) every
tool returns the consolidated all-entities view.

## Conversation history (tenant-scoped)

`assistant_conversations` + `assistant_messages` live in each tenant DB —
conversation content is tenant business data and inherits tenant isolation.
Every conversation read filters `(organization_id == jwt_org, user_id ==
current_user.id)`: a user sees only their own threads, and another user's /
tenant's conversation id **404s** (not 403, so it can't enumerate).

## Token budget + usage meter (control plane)

`assistant_usage` lives in the control plane (next to `extraction_usage`):
billing is a per-org concern and one upsert-on-`(org, period)` row enforces the
cap without fanning a sum across every tenant DB on each call.

- **Enforcement** — `usage.assert_within_budget` at the **top of `run_turn`**,
  before any adapter/model/tool work. It takes a **`SELECT … FOR UPDATE`** on
  the `(org, period)` meter row so concurrent turns for the same org serialize
  on it — without the lock two simultaneous `/chat` requests both read
  `used < budget` and both run, overshooting the cap (a check-then-act race).
  The lock is held until the control transaction commits at request end. Budget
  `0` disables the cap (matching the `AP_MAX_CONCURRENT_SESSIONS=0` convention).
  Per-org override in `Organization.settings.assistant.monthly_token_budget`
  beats the platform default. (A single turn is still bounded by
  `max_tokens × AP_ASSISTANT_MAX_TOOL_HOPS`, so post-hoc counting can overshoot
  by at most one turn's worth — the lock removes the *unbounded* concurrent
  overshoot.)
- **Refusal contract** — `AssistantBudgetExceeded` → **HTTP 429** with
  `{"detail": "...", "code": "assistant_budget_exceeded", "used", "budget",
  "period"}`. No tool runs, no model call, nothing persisted.
- **Recording** — `usage.record` upserts the meter (atomic
  `INSERT … ON CONFLICT DO UPDATE` increment) but **does not commit**: it flushes
  onto the request's `control_db` and rides the request commit boundary
  alongside the tenant-side conversation + audit rows, so the three commit or
  roll back together. The meter is the single source of truth for the cap and
  for `GET /api/assistant/usage`.

## Audit trail

Every tool call writes one append-only audit row **before** the tool returns
data, via `dispatch_audit` (action `assistant.tool_invoked`, entity_type
`assistant_conversation`). A multi-hop claude turn writes one row per hop. The
`details` payload carries the tool name + a **PII-safe arg shape** — never
values: `find_invoices_by_text` logs `{"query_len": N, "k": K}` (never the query
text); `list_invoices` logs `{"status": [...], "has_amount_filter": bool, ...}`;
`get_vendor_spend` logs `{"period", "top_n"}`. No amount, bank, or tax value
ever enters the trail.

The `find_invoices_by_text` **answer/snippet** (not the audit row) does include
the matched invoice's `amount` — by design: it's surfaced only to an already
-authenticated same-tenant employee, and it's the same field `list_invoices`
returns to that user. PII (`vendor_tax_id`, `bank_details`, addresses) is held
out by the `_SNIPPET_FIELDS` allowlist; amount/invoice-number/dates are the
non-confidential summary fields. If per-role amount confidentiality is ever
required, gate the snippet's `amount` on role — tracked as a future option, not
a current gap.

The tool's read runs inside a SAVEPOINT (`begin_nested`) so a failing query
(e.g. a schema-drifted tenant) rolls back just the tool — the audit row and the
message persistence survive, and the turn returns a clean error invocation
instead of a 500.

## API

All under `/api/assistant`, behind `get_current_user` (auth-before-everything),
employee roles only (`admin`/`ap_manager`/`ap_clerk`/`cfo`). Vendor-portal JWTs
are rejected by `get_current_user`.

| Method | Path | Body / Query | Response |
|--------|------|--------------|----------|
| POST | `/api/assistant/chat` | `{message, conversation_id?}` | `ChatResponse` |
| GET | `/api/assistant/conversations` | `?limit&offset` | `{items, total}` |
| GET | `/api/assistant/conversations/{id}` | — | `{conversation, messages}` |
| GET | `/api/assistant/usage` | — | `UsageResponse` |

`ChatResponse.tool_invocations[*].result` is the verbatim tool ReturnModel dump
— already chartable (e.g. `get_vendor_spend.vendors[]` → bar chart). The future
chart UI reads the structured `result`; the top-level `answer` is the prose.

## Configuration (`AP_` prefix)

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_ASSISTANT_PROVIDER` | `mock` | `mock` (local-first) \| `claude` |
| `AP_ASSISTANT_MODEL` | (empty) | Model id; empty → `AP_EXTRACTION_MODEL` (claude-opus-4-8 family) |
| `AP_ASSISTANT_MONTHLY_TOKEN_BUDGET` | `200000` | Per-org/month token cap; `0` disables |
| `AP_ASSISTANT_MAX_TOOL_HOPS` | `4` | Claude tool-use loop cap (cost bound) |
| `AP_ANTHROPIC_API_KEY` | (empty) | Reused from extraction — **no new secret**. Empty → auto-downgrade `claude`→`mock` |

## Migration

`0032_assistant` is branch-aware (gated on the `invoices` table): tenant DBs get
`assistant_conversations` + `assistant_messages`; the control plane gets
`assistant_usage`. Apply with
`AP_MIGRATE_TENANT=ap_acme alembic upgrade head` →
`python scripts/migrate_all_tenants.py` → `alembic upgrade head` (control). Fresh
tenants get the conversation tables via `tenant_provisioning._create_tenant_tables`
(`create_all`); `assistant_usage` is in `CONTROL_TABLES` so it's never created on
a tenant DB.

## Deferred (tracked follow-ups)

1. **SSE streaming** — `POST /api/assistant/chat/stream` (`StreamingResponse`,
   claude SSE passthrough). Trigger: the frontend chat UI needs token-by-token
   rendering.
2. **Chart-rendering UI / example-prompt empty state** — frontend only; the API
   already returns chartable structured `result`.
3. **BYO-LLM beyond claude** (openai/etc.) — add adapters under the same
   registry; the registry is already open for it.
4. **Shared `invoice_queries.py`** — `list_invoices` re-builds the filtered
   SELECT because `app/api/invoices.py` is frozen this session (in-flight
   multi-entity work). Durable fix: extract the canonical filter/paginate into
   `app/services/` and have both the invoices router and the tool call it.
   Trigger: once the multi-entity invoices work lands and the file is editable.
