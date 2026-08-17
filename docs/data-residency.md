# Data residency (GDPR / CCPA)

Pin a tenant's data — its Postgres database and its object storage
(MinIO/S3) — to a geographic region (`us`, `eu`, `uk`, `ca`, `au`). This is
the GDPR/CCPA *data-residency* control: a customer in the EU can require that
their invoices, attachments, and audit trail stay within the EEA, and a
customer in the UK/Canada/Australia can require the equivalent for their
jurisdiction.

The **database-per-tenant** architecture is what makes this tractable: each
tenant already gets its own database (`feoh_<slug>`) and its own MinIO/S3 key
prefix, so pinning a tenant to a region is a *placement* decision (which
cluster does its DB live on, which bucket/endpoint holds its files) rather than
a schema change. No per-row region column, no cross-tenant data commingling to
untangle.

This page documents the model. The configuration knob and the documented
placement targets ship now; the multi-region infra that physically routes a
tenant onto a regional cluster is future work (see **Current reality** below).

## Where the region is stored

On the existing `Organization.settings` JSONB column, same pattern as
`settings.retention` / `settings.sso`:

```json
{
  "residency": { "region": "eu" }
}
```

**No new column, no migration.** A tenant with no `residency` block is treated
as the platform default region.

`backend/app/services/data_residency.py` is the single source of truth:

| Symbol | Meaning |
|--------|---------|
| `SUPPORTED_REGIONS` | `("us", "eu", "uk", "ca", "au")` — the region tokens the API accepts. |
| `DEFAULT_REGION` | `"us"` — fallback when a tenant sets no region. A **module constant**, not an env var: the single-region reality is codified in code until multi-region infra ships. |
| `resolve_region(org)` | Reads `org.settings["residency"]["region"]`, falls back to `DEFAULT_REGION`. Never raises — a missing/malformed/unknown value all degrade to the default (a placement read must never break a request). |
| `REGION_PLACEMENT` | Per-region documented target: intended DB cluster + object-storage bucket/endpoint. The "model" the future provisioning + connection layer resolves against. |
| `get_region_placement(region)` | The placement for a region; falls back to the default region's placement for an unknown key. |
| `check_residency_alignment(org, deployed_region)` | Advisory: reports (never blocks) whether a tenant's configured region is the one the stack is actually deployed in. Returns a `ResidencyAlignment` — see [Alignment](#alignment-is-the-pin-honoured-today) below. Logs a PII-free WARNING on a genuine mismatch or a misconfigured `deployed_region`. |
| `ResidencyAlignment` | Frozen dataclass: `status` (`aligned`/`misaligned`/`unknown`), `aligned` (`True`/`False`/**`None`**), `configured_region`, `deployed_region`, `reason`. |

## Where the *deployed* region comes from

The tenant's pin says where its data **should** live. Where the stack actually
runs is a fact about the deployment, not about any tenant — so it is operator
env, not settings-JSON:

| Variable | Default | Meaning |
|---|---|---|
| `FEOH_DEPLOYED_REGION` | (empty) | The region this stack declares it runs in. One of `SUPPORTED_REGIONS`. **Empty = unknown / cannot attest.** |

It is deliberately **not validated at boot**. The value is advisory — nothing
routes, blocks, or moves data on it — and refusing to start over an advisory
field trades a wrong answer for an outage. An unrecognised value reports
`unknown` with a reason instead (below).

`backend/.env.development` sets `us`, matching the single-region reality the
platform documents, so the signal is exercisable under `pnpm dev`: an unpinned
tenant reads `aligned`, and pinning one to `eu` flips it to `misaligned`. That
file is loaded by `main.py` (the local-dev entrypoint) only, so it cannot leak
into a deployed environment.

## Alignment: is the pin honoured today?

`check_residency_alignment(org, FEOH_DEPLOYED_REGION)` answers one question and
never acts on the answer. Three states, because two of them are not comparisons:

| `status` | `aligned` | When | `reason` |
|---|---|---|---|
| `aligned` | `true` | The pin equals the declared deployed region. | — |
| `misaligned` | `false` | The pin differs — a commitment we are not physically honouring yet. | — |
| `unknown` | **`null`** | `FEOH_DEPLOYED_REGION` is unset. | `deployed_region_unset` |
| `unknown` | **`null`** | `FEOH_DEPLOYED_REGION` is set to something outside `SUPPORTED_REGIONS` (e.g. `eu-central-1` for `eu`). | `deployed_region_unrecognised` |

`aligned` is tri-state on purpose. Defaulting an unset deployed region to
`DEFAULT_REGION` would hand an EU-pinned tenant a green light nothing verified —
the exact failure mode a residency control exists to prevent. **Unknown is a
legitimate answer; a fabricated `true` is not.** And an unrecognised token
reports `unknown` rather than comparing literally, because a single typo would
otherwise mark *every* tenant misaligned and bury the ones that genuinely are.

## Supported regions

| Token | Region | Why it's distinct |
|-------|--------|-------------------|
| `us` | United States | Default — where the single-region stack runs today. CCPA. |
| `eu` | European Union | GDPR data-residency — data kept within the EEA. |
| `uk` | United Kingdom | Post-Brexit UK GDPR; a separate jurisdiction from `eu`. |
| `ca` | Canada | PIPEDA / provincial data-residency requirements. |
| `au` | Australia | Privacy Act / APP cross-border-disclosure restrictions. |

Each region maps to a documented placement in `REGION_PLACEMENT` — an intended
Postgres cluster name (e.g. `feoh-pg-eu-central-1`), an object-storage bucket
(e.g. `feoh-tenant-files-eu`), and the S3 region + endpoint for that bucket.
These are target names the future connection/provisioning layer will resolve
against; nothing here is wired into live infra yet.

## API

Wired into the existing `/api/organization` router
(`backend/app/api/organization.py`):

| Method | Path | RBAC | Purpose |
|--------|------|------|---------|
| `GET` | `/api/organization/data-residency` | any authenticated org user | Effective region + default + supported list + the documented placement for the effective region + the advisory `alignment` block. |
| `PUT` | `/api/organization/data-residency` | **admin only** | Set the region. Validates against `SUPPORTED_REGIONS` (422 on an unsupported value *before* any write), writes `settings["residency"]["region"]` via `flag_modified`, and audits `organization.residency_updated` into the tenant trail (PII-free — region tokens only). Returns the same payload as GET, alignment included — so an admin sees immediately whether the pin they just made is honoured. |

Both responses carry:

```json
{
  "region": "eu",
  "default_region": "us",
  "supported_regions": ["us", "eu", "uk", "ca", "au"],
  "placement": { "db_cluster": "feoh-pg-eu-central-1", "...": "..." },
  "alignment": {
    "status": "misaligned",
    "aligned": false,
    "deployed_region": "us",
    "reason": null
  }
}
```

The alignment block is **reporting only** — no request path branches on it, no
route refuses on it, no data moves because of it.

The read is gated to the same roles as `GET /api/organization` (any authed
user); only the mutate path is admin-only, matching the records-management
posture of the retention-policy endpoint.

Changing the region is a **configuration** change — it records *where the data
should live*. It does **not** itself migrate any data; physically moving a
tenant's DB + files between regions is a separate infra operation (see below).

## Current reality (single region)

The whole platform runs in **one** region today, and `DEFAULT_REGION = "us"`
is where that region lives. So:

- A tenant pinned to `eu` is *configured* for the EU but its data still
  physically lives in the single (US) region until multi-region infra exists.
- `check_residency_alignment(org, FEOH_DEPLOYED_REGION)` surfaces exactly this
  gap, and it is no longer log-only: the verdict rides `GET`/`PUT
  /api/organization/data-residency` as the `alignment` block and renders on the
  `/organization` **Data Residency** panel, so the tenant's own admin — not just
  an operator reading logs — can see that the commitment is not yet physically
  honoured. Advisory throughout; nothing blocks.

## The UI

`/organization` → **Data Residency** (`frontend/src/routes/organization/+page.svelte`):
the region picker (the platform default is marked as such), the documented
placement target for the selected region, and the alignment verdict as a tinted
box — green for `aligned`, amber for `misaligned`, muted for `unknown` — each
carrying the standing "advisory only, nothing is blocked" line. Save is enabled
only when the selection differs from what is persisted, and a refused save
(non-admins get a 403 from the backend) snaps the control back to the persisted
region rather than leaving a pin on screen that was never made.

This is deliberate: the roadmap asks us to **document the model even before
multi-region infra ships**, so the configuration surface, the placement map,
and the alignment check land now and the infra follows.

## Future multi-region plan

When multi-region infra ships, the placement map becomes load-bearing:

1. **Regional Postgres clusters** — one per region (the `db_cluster` values in
   `REGION_PLACEMENT`). Tenant provisioning routes a new tenant's `feoh_<slug>`
   database onto the cluster for its region; the per-tenant engine pool in
   `app/database.py` resolves the host from `resolve_region(org)` →
   `get_region_placement(region)["db_cluster"]` instead of a single global host.
2. **Regional object-storage buckets** — one per region (the `s3_bucket` /
   `s3_endpoint` / `s3_region` values). `services/storage.py` selects the
   bucket + endpoint for the tenant's region when uploading/downloading files,
   so attachments never leave the jurisdiction.
3. **Migration path** — changing a *live* tenant's region triggers an operator
   workflow that copies its DB + objects to the new region and re-points the
   connection layer; the `PUT` endpoint records the intent + audits it, and the
   alignment check confirms convergence afterwards.

Until then the model is documented, the region is configurable + audited, and
the alignment check tells operators which tenants are waiting on the infra.
