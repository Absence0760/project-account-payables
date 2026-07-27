# Centralized audit-log shipping

SOC 2 control. The `audit_log` table lives **inside each tenant's database**,
where a determined tenant-admin (or a compromised one) could theoretically
edit rows. To give auditors a tamper-evident, append-only copy of every
audit event, the backend runs a background shipper that pulls unshipped
rows out of every tenant DB and writes them to one or more WORM
(write-once-read-many) sinks: **CloudWatch Logs** and **S3 with Object Lock**.

- Code: `backend/app/services/audit_log_shipper.py`
- Adapters: `backend/app/services/audit_shipping/`
- SOC 2 mapping: `../../docs/soc2-readiness.md` → "Logging + monitoring"
- Migration: `alembic/versions/0010_audit_log_shipping.py`

## How it works

1. Every `FEOH_AUDIT_SHIPPING_INTERVAL_SECONDS` (default 60s), a background
   asyncio task wakes up.
2. The control plane's `Organization` table is queried for every tenant
   DB name.
3. For each tenant, the shipper opens a fresh async engine and runs:

   ```sql
   SELECT * FROM audit_log
   WHERE shipped_at IS NULL
   ORDER BY created_at ASC
   LIMIT :batch_size;   -- default 500
   ```

4. The batch is converted to `AuditLogRow` dataclasses and handed to every
   adapter listed in `FEOH_AUDIT_SHIPPING_PROVIDERS`.
5. **All** adapters must succeed before the tenant's rows get marked
   `shipped_at = now()`. If any one raises, the shipper logs a WARNING,
   leaves `shipped_at` NULL, and the next tick retries the entire batch.
   CloudWatch Logs and S3 both have at-least-once semantics, so a replay
   may produce duplicate events downstream — that's documented and
   acceptable.
6. On shutdown the task is cancelled cleanly via `main.lifespan`.

### Why one batch at a time per tenant

Simple + bounded. The largest tenant DB governs how quickly we drain
— if you notice rows aging past a few ticks of the interval, raise
`FEOH_AUDIT_SHIPPING_BATCH_SIZE`. A 500-row batch gzips to ~80KB of JSONL;
CloudWatch accepts up to 10,000 events per `PutLogEvents`, so there's
plenty of headroom.

### Why a per-tick engine

The shipper doesn't own the cached tenant-engine pool (`database.get_tenant_engine`).
It spins one up per tenant per tick and disposes it in `finally`, matching
`extraction_reaper`. Cheaper than plumbing the cache through a background
task that doesn't own a request-scoped session, and it bounds connection
fan-out under a runaway sweep.

## Configuration

All env vars have the `FEOH_` prefix.

| Variable | Default | Purpose |
|---|---|---|
| `FEOH_AUDIT_SHIPPING_ENABLED` | `false` | Master switch. Default off so local dev doesn't fire AWS calls. |
| `FEOH_AUDIT_SHIPPING_INTERVAL_SECONDS` | `60` | How often the shipper sweeps. |
| `FEOH_AUDIT_SHIPPING_BATCH_SIZE` | `500` | Max rows pulled per tenant per tick. |
| `FEOH_AUDIT_SHIPPING_PROVIDERS` | `mock` | Comma-separated adapter names. Typical prod value: `cloudwatch,s3_objectlock`. |
| `FEOH_AUDIT_SHIPPING_CLOUDWATCH_GROUP` | `/ap/audit` | CloudWatch Logs group name. |
| `FEOH_AUDIT_SHIPPING_S3_BUCKET` | (empty) | Object-Lock-enabled S3 bucket for the WORM copy. Required when `s3_objectlock` is enabled. |

## Adapters

### `mock` — in-memory sink (default, local dev + tests)

Records every shipped row into a module-level list. Useful for tests
and as a "shipping enabled, but don't actually ship anywhere yet" stub.
Not durable — do not use in production.

### `cloudwatch` — AWS CloudWatch Logs

Writes each row as a single JSON log event. Events are partitioned by
log stream: one stream per `(tenant_db, UTC-date)`, e.g.
`feoh_acme/2026-04-21`. The log group is controlled by
`FEOH_AUDIT_SHIPPING_CLOUDWATCH_GROUP`.

Why one stream per tenant per day? CloudWatch charges by ingested
bytes, not stream count, and pre-partitioning by tenant + day lets
auditors answer "show me every event for tenant X on day Y" with a
simple filter rather than a Log Insights scan.

The adapter lazily creates the log group + streams on first use; the
`ResourceAlreadyExistsException` is swallowed so repeated ticks don't
bang on `CreateLogStream` with errors.

### `s3_objectlock` — S3 with Object Lock (Governance or Compliance mode)

Each batch becomes a single gzip-compressed JSONL object:

```
s3://<bucket>/audit/<tenant_db>/<YYYY>/<MM>/<DD>/<ISO-stamp>-<uuid>.jsonl.gz
```

**S3 Object Lock caveats:**

- Object Lock **must** be enabled at bucket creation time — it cannot
  be turned on later. The adapter's `test_connection()` checks this
  via `get_object_lock_configuration` and refuses to ship if the bucket
  isn't configured.
- The bucket **must** have a default retention period set (Governance
  or Compliance mode). Without one, objects are written normally and
  can be deleted — defeating the WORM guarantee.
- Retention period + mode are provisioned by infra (Terraform), not by
  this adapter. See `infra/` for the bucket definition.
- SOC 2 auditors typically want **≥ 365 days** retention in
  **Compliance mode** for audit logs. Governance mode lets privileged
  IAM principals delete early; Compliance mode locks even root.
- Versioning must also be enabled — S3 Object Lock is implemented on
  top of object versions.

## How to add a new shipping provider

```python
# backend/app/services/audit_shipping/splunk_adapter.py

from app.services.audit_shipping.base import AuditLogRow, AuditShippingAdapter
from app.services.audit_shipping.dispatcher import register_audit_shipping_adapter


@register_audit_shipping_adapter("splunk")
class SplunkAdapter(AuditShippingAdapter):
    provider_name = "splunk"

    def __init__(self, config: dict):
        super().__init__(config)
        # read endpoint, HEC token, etc. from settings

    async def ship(self, rows: list[AuditLogRow]) -> None:
        # raise on failure — the shipper uses that to decide whether to
        # mark `shipped_at`. At-least-once semantics are fine.
        ...

    async def test_connection(self) -> bool:
        ...
```

Then:

1. Import the new module in `audit_shipping/__init__.py` so its
   registration decorator runs.
2. Add the provider name to `FEOH_AUDIT_SHIPPING_PROVIDERS`
   (comma-separated with existing ones).
3. Add a test to `tests/test_audit_shipping.py`.

## Schema

Migration `0010_audit_log_shipping`:

- Adds `shipped_at TIMESTAMPTZ NULL` to the tenant-local `audit_log` table.
- Adds a partial index `ix_audit_log_shipped_at_null ON audit_log(created_at)
  WHERE shipped_at IS NULL` so the shipper's "unshipped rows, oldest first"
  query stays cheap as the audit log grows.
- Guarded by `_has_table("audit_log")` — no-op on the control plane.

## DB-level immutability (SOX) and the `shipped_at` carve-out

Migration `0022_sox_audit_immutable` (DDL in `app/services/audit_immutability.py`)
installs a pair of `BEFORE` triggers on `audit_log`:

- `audit_log_no_delete` — rejects **every** DELETE.
- `audit_log_no_update` — rejects every UPDATE that changes any column **other
  than `shipped_at`**.

This is the durable SOX control: the app already exposes no PATCH/DELETE route
(`tests/test_audit_append_only.py`), but a rogue ORM call or a direct `psql`
session would bypass that — the trigger does not.

**The `shipped_at` carve-out is load-bearing for this shipper.** The trigger
function compares all non-`shipped_at` columns between OLD and NEW; a pure
`shipped_at = now()` stamp (the only write the shipper performs, step 3 above)
passes, while re-stamping `shipped_at` *alongside* any other edit is rejected.
If you ever change how the shipper marks rows shipped, it must remain a
`shipped_at`-only UPDATE or the trigger will refuse it.

The triggers are installed on **every** tenant DB: migration `0022` fans out
across existing tenants (`scripts/migrate_all_tenants.py`), and
`tenant_provisioning._create_tenant_tables` installs the same DDL on freshly
provisioned tenants (which are created via `create_all`, not Alembic).
Idempotent (`CREATE OR REPLACE` / `DROP ... IF EXISTS`), guarded by
`_has_table("audit_log")`. Covered by `tests/test_audit_immutable.py`,
including an assertion that the shipper's stamp still succeeds.

## Operational notes

- **Disabled in local dev**. The default `audit_shipping_enabled=false`
  means your laptop doesn't need AWS credentials.
- **Replays on retry** are expected and safe — both CloudWatch and S3
  treat the same row ID as a new event / new object. Downstream tooling
  (SIEM, auditor queries) should dedup on `audit_log.id` if exact
  uniqueness matters.
- **Tenant scale**. With 1,000 tenants and a 60-second interval, the
  shipper opens + disposes ~1,000 engines per minute. Each spin-up is
  cheap (< 10ms on warm DNS), but if this ever becomes a hotspot the
  cache in `database.get_tenant_engine` is the right place to plug in.
- **Back-pressure**. If shipping falls behind (adapter latency > tick
  interval), the backlog builds up inside `audit_log`. The partial
  index keeps the SELECT cheap; the shipper just needs more ticks to
  catch up. There's no hard cap on backlog size.
- **No alerting yet**. Out of scope for this PR; see the SOC 2 readiness
  doc's "Alerting" row (pending).
