# Retention policies (SOX records management)

Configurable per-record-class retention periods plus an audited enforcement
sweep that archives records past their window through a **privileged, audited
path** — never a raw `DELETE`. It composes with the audit-log immutability
trigger: `audit_log` rows are WORM and are never deleted.

## Configuration — not hardcoded

Retention windows live on `Organization.settings.retention`, keyed
`<record_class>_months`:

```json
{ "retention": { "invoices_months": 84, "audit_log_months": 84 } }
```

`resolve_retention_months(settings, record_class)` (in
`services/retention_sweep.py`) resolves the effective window: per-org override →
platform default `FEOH_RETENTION_DEFAULT_MONTHS` (84 = 7 years, the common
SOX/IRS baseline). It never raises — a malformed/missing value degrades to the
default.

### API — `GET` / `PUT /api/retention-policy` (admin only)

`app/api/retention.py`:

- **GET** returns the effective policy per record class, the platform default,
  and whether the sweep is enabled.
- **PUT** updates one or more `<class>` windows (months, `> 0`). Unknown record
  classes or non-positive windows are rejected `422` before any write. Every
  mutation writes a `retention_policy.updated` audit row into the **tenant**
  trail (via the self-committing `dispatch_auth_audit`, since the settings
  themselves live on the control plane). PII-free — only class names + month
  windows.

Record classes the engine understands: `invoices`, `audit_log` (`RECORD_CLASSES`).

## Enforcement sweep — `services/retention_sweep.py`

A long-lived asyncio loop (mirrors `contract_renewal` / `qms_sync`) started in
`app/main.py` lifespan, **disabled by default** behind `FEOH_RETENTION_ENABLED`,
interval `FEOH_RETENTION_INTERVAL_SECONDS` (default daily). It sweeps every tenant
DB; one tenant's failure is logged but never halts the sweep.

### Business records (deletable) — soft-archive, idempotent

For each tenant, invoices in a **terminal** state (`done` / `paid`) whose
`created_at` is older than the `invoices_months` window get an `archived_at`
marker stamped into their `meta` JSONB bag. No row is destroyed; the marker is
the privileged, fully-reversible archival action — and **no schema change** is
needed. The sweep is idempotent: an already-marked invoice is skipped, so a
re-run never double-archives. In-flight (non-terminal) invoices are never
archived regardless of age.

### Audit records (WORM) — verify, never delete

**CRITICAL: the sweep never deletes `audit_log` rows.** Migration 0022 installs
a `BEFORE DELETE` trigger that rejects every delete (and every UPDATE except
`shipped_at`), so a deletion would be refused by Postgres anyway — but the sweep
must not even attempt it. For the `audit_log` class, "retention" means
*verifying* that rows past the window have been WORM-shipped (`shipped_at` set)
and recording a manifest:

- `audit_rows_overdue` — rows past the `audit_log_months` window.
- `audit_rows_overdue_unshipped` — of those, how many still have
  `shipped_at IS NULL` (the WORM sink is behind; an operator should investigate
  `audit_log_shipper`).

### Audited manifest

When a sweep archives anything or observes overdue audit rows, it writes a
`retention.archived` audit row (system actor, PII-free `details`): the resolved
window months per class, the count + ids of archived invoices, and the audit
overdue/unshipped counts, plus a note that audit rows are immutable and never
deleted. An idle tenant writes no manifest (no no-op spam).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `FEOH_RETENTION_ENABLED` | `false` | Master switch for the enforcement sweep. Keep `false` in local dev; flip on in deployed envs. |
| `FEOH_RETENTION_INTERVAL_SECONDS` | `86400` | Sweep interval. |
| `FEOH_RETENTION_DEFAULT_MONTHS` | `84` | Platform-default window (months) when an org sets no per-class override. |

## Why no migration

This first slice is **config + an audited archival sweep** that acts only on
already-deletable / soft state (the existing `Invoice.meta` JSONB marker) and
verifies WORM shipment for the immutable audit class — so it needs **no schema
change**. A future slice that wants a first-class `Invoice.archived_at` column
(for indexed exclusion of archived rows from list endpoints) would add it then.

## Tests

`backend/tests/test_retention.py`: resolver (override → default, malformed →
default); per-tenant failure isolation; sweep archives only overdue terminal
invoices; idempotent (no double-archive); writes the manifest; **never deletes
audit rows** (composes with the immutability trigger); policy GET/PUT + audit
row; `422` on unknown class / non-positive window; RBAC (admin-only, 401/403).
