# Periodic access reviews (SOX)

A SOX access-control requirement is to **periodically review** who holds
privileged access and prove that access is still used. This feature is the
review surface for that control: it flags users holding elevated roles whose
privileged permissions have gone unused, and records a reviewer's sign-off.

Two pieces:

- `app/services/access_review.py` — pure, compute-on-read derivation of the
  per-user "last privileged action" index + dormancy verdict.
- `app/api/access_reviews.py` — the `/api/access-reviews` router (review list +
  acknowledge), admin/CFO gated, both routes audited.

There is **no database migration**. The "last elevated use" index is derived
live from the existing append-only `audit_log`, so it can never drift from a
denormalised column and inherits the audit table's immutability for free.

## What counts as "elevated"

```python
ELEVATED_ROLES = {admin, ap_manager, cfo}
```

`ap_clerk` is deliberately excluded — it's the baseline operator role, not a
privileged grant, so reviewing it as "unused elevated access" is noise.

Role NAME is not the whole story, though: a **custom role** (granular
permissions, `app/api/permissions.py`) can grant a fraud-sensitive permission —
`payment.execute`, `payment.void`, `payment_run.approve`,
`vendor.bank_change.approve`, `vendor.block`, `user.manage` — to a role named
anything at all. A user holding *only* such a custom role is still flagged
elevated: `compute_access_review` also unions each user's `effective_permissions`
(the same helper `get_current_user` uses) against `ELEVATED_PERMISSIONS`, so the
review can't be blinded by a role name that doesn't say "admin".

## What counts as a "privileged action"

The last-action index aggregates `MAX(audit_log.created_at)` per actor, scoped to
the org, **excluding read verbs** — any action whose suffix is `.viewed` or
`.exported`. A read (`vendor.viewed`, `audit.exported`) means the user merely
*looked* at a record; it is not evidence that their elevated **write** permission
is still needed. So a CFO who only ever opens dashboards is correctly surfaced as
dormant for their elevated mutate rights.

Mutating actions (`invoice.approved`, `payment.created`, `vendor.updated`, …) do
reset the clock.

## Dormancy

A user is flagged **DORMANT** when:

- their last mutating privileged action is older than
  `FEOH_ACCESS_REVIEW_DORMANT_DAYS` (default **90**), **or**
- they have never produced a mutating audit row (`last_privileged_action_at` is
  `null`, `days_since` is `null`).

The list is sorted dormant-first (never-acted ahead of long-idle), so the review
surface leads with the access most in need of revocation.

Users are resolved from the **control plane** (`User` + their roles); last-action
timestamps from the **tenant** `audit_log`. Inactive users (`is_active = false`)
are dropped — you can't review access for someone who can't log in.

## Endpoints (`/api/access-reviews`)

Both routes require `admin` or `cfo` (the reviewer privilege) and are audited.

### `GET /api/access-reviews`

Returns the computed list:

```json
{
  "dormant_after_days": 90,
  "generated_at": "2026-06-17T12:00:00+00:00",
  "total": 3,
  "dormant_count": 2,
  "users": [
    {
      "user_id": "…",
      "full_name": "Jane Admin",
      "email": "jane@acme.test",
      "roles": ["admin"],
      "last_privileged_action_at": null,
      "dormant": true,
      "days_since": null
    }
  ]
}
```

This is a sensitive read (it enumerates who holds privileged access), so it
writes an `access_review.viewed` audit row via `log_access`. The `details` carry
only counts + the dormancy window — never a regulated value.

### `POST /api/access-reviews/acknowledge`

The **review-workflow closure**: records that a reviewer completed the review for
the period.

- Writes an `access_review.completed` audit row (tenant trail, append-only).
- Stamps `Organization.settings.access_review` on the control-plane org:
  ```json
  { "last_completed_at": "2026-06-17T12:00:00+00:00", "last_completed_by": "<reviewer-user-id>" }
  ```

Idempotent-friendly: re-acknowledging simply re-stamps with the latest timestamp
+ reviewer — no error, no duplicate side effect.

## Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `FEOH_ACCESS_REVIEW_DORMANT_DAYS` | `90` | Dormancy window. A user whose last *mutating* privileged action is older than this (or who has never acted) is flagged DORMANT in `GET /api/access-reviews`. |

## Invariants honoured

- **Auth before everything / RBAC** — both routes carry `require_roles(ADMIN, CFO)`.
- **Tenant isolation** — users + audit rows are filtered by `user.organization_id`;
  the tenant DB is resolved via `get_tenant_db` (JWT `org`-claim cross-checked),
  the control DB via `get_control_db`. No hardcoded tenant DB name.
- **Audit trail is append-only** — the acknowledge writes through `dispatch_audit`,
  the same WORM-shipped path as every other audit row; the review read writes
  through `log_access`.
- **PII out of logs** — only counts, role names, names/emails, and timestamps are
  surfaced; no tax id / bank number / PAN ever enters the response or audit details.
- **No migration** — compute-on-read; the index is derived from `audit_log`.
