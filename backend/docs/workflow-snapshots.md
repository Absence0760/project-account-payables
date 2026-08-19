# Workflow Snapshots & Single Active Workflow

## Problem

When a user edits or swaps a workflow definition, invoices already in flight should continue following the rules they started with. Without this, changing a workflow mid-process could cause invoices to skip steps, require unexpected approvals, or export to the wrong ERP format.

## Design

### Snapshot on Creation

When an invoice is uploaded, the system:

1. Looks up the **active workflow definition** for the organization
2. Copies the definition's `steps_config` into `WorkflowInstance.steps_config_snapshot`
3. Links the instance to the definition via `definition_id` (historical reference)

From that point on, the invoice follows the **snapshot**, not the live definition. Edits to the workflow definition have no effect on existing invoices.

```
WorkflowDefinition (live, editable)
  └── steps_config: { steps: [...] }     ← used for NEW invoices

WorkflowInstance (per invoice, frozen)
  ├── definition_id: FK → WorkflowDefinition  ← historical link
  └── steps_config_snapshot: { steps: [...] } ← frozen at upload time
```

### When there is no snapshot to read — fail CLOSED

Not every invoice has a `WorkflowInstance`. `email_intake` and `peppol_receive`
used to insert the invoice row without one (both ingest paths handed straight to
extraction); **both now call `create_workflow_instance` right after the flush**,
exactly as every other ingress does, so a freshly-ingested invoice is governed
by the config frozen at ingest, carries `WorkflowStep` rows, and is visible to
the step-based approval-queue reads and `GET /api/invoices/{id}/workflow`. Any
legacy or directly-inserted row still has none, so the fail-closed resolver
below stays load-bearing (it is what covers every invoice ingested before that
change).

The approval controls in `services/review` used to read their config *only* off
`steps_config_snapshot` and return early when there was none. That was a hole,
not a neutral default: `{}` skipped the `max_invoice_amount` cap, the
`require_cfo_above` gate, the structuring guard **and** the named-approver
check, so a $50,000 invoice that arrived by email cleared a $1,000 CFO gate on a
lone `ap_manager`'s approval.

`review.resolve_approval_config(db, invoice, instance)` is now the single
resolver both the segregation/named-approver gates and the money gates read:

1. the invoice's frozen `steps_config_snapshot` — unchanged, and still
   authoritative whenever one exists;
2. only when there is none, the org's currently-active definition, resolved
   **read-only** by `workflow_engine.resolve_active_workflow_definition` (never
   the get-or-CREATE variant — a definition must not appear as a side effect of
   an approval);
3. `{}` only when the org has no active definition at all, or its definition has
   no approval step — which is genuinely nothing to enforce.

This does **not** weaken the frozen-snapshot invariant. An in-flight invoice
with a snapshot is still governed by the config it entered under, even after the
org tightens the live definition; step 2 fills a gap that previously read as
"ungated", it never overrides a snapshot. Covered by
`backend/tests/test_approval_without_instance.py` and the unit cases in
`test_approval_thresholds.py`.

`review.assign_reviewer` had the mirror-image bug from the same cause: a missing
instance made it `return` before its audit row and notification, so an
email-intake invoice could be assigned with no `invoice.assigned_for_review`
trail, no email/Slack/Teams alert, and no approval action token minted. Only the
step-row update is now conditional on the instance.

### Single Active Workflow

Only one workflow definition can be active per organization at any time.

- **Activating** a workflow automatically **deactivates** all others in that org
- **New workflows** are created as **inactive** — the user must explicitly activate them
- **Deleting** the default workflow is not allowed
- The default workflow is auto-created on first access if no workflows exist

### How `is_step_enabled` Works

The function checks whether a step (extraction, approval, erp_export) is enabled:

1. If an `invoice_id` is provided, it reads from that invoice's `steps_config_snapshot`
2. Otherwise, it reads from the org's active workflow definition (used during upload before the instance exists)

```python
# During upload — no instance yet, reads from active definition
extraction_enabled = await is_step_enabled(db, org_id, "extraction")

# After upload — reads from the frozen snapshot
extraction_enabled = await is_step_enabled(db, org_id, "extraction", invoice_id=invoice.id)
```

## Data Model

### WorkflowDefinition (template)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `name` | String | Display name |
| `description` | Text | Optional description |
| `steps_config` | JSONB | The live step configuration |
| `is_active` | Boolean | Only one per org can be true |
| `is_default` | Boolean | Auto-created system default |
| `organization_id` | UUID | Tenant scoping |

### WorkflowInstance (per invoice)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `definition_id` | UUID FK | Which template it was created from |
| `invoice_id` | UUID FK | The invoice this instance tracks |
| `steps_config_snapshot` | JSONB | Frozen copy of `steps_config` at creation time |
| `current_step` | Integer | Current step index |
| `state` | String | `active`, `completed`, `failed` |
| `state_data` | JSONB | Runtime data (retry counts, errors, etc.) |

## Example Scenario

1. **Workflow A** is active: extraction enabled, approval required, ERP export as XML
2. User uploads **Invoice #1** → snapshot freezes Workflow A's config
3. User edits Workflow A: disables extraction, changes export to CSV
4. **Invoice #1** still follows the original snapshot (extraction, approval, XML)
5. User uploads **Invoice #2** → snapshot freezes the updated config (no extraction, CSV)
6. User creates **Workflow B** (inactive), then activates it (Workflow A auto-deactivates)
7. User uploads **Invoice #3** → snapshot freezes Workflow B's config
8. Invoices #1 and #2 are unaffected — they follow their own snapshots

## API Behavior

### Activation

```
PATCH /api/workflows/{id}
{ "is_active": true }
```

- Sets the target workflow as active
- Deactivates all other workflows for the org
- Returns the updated workflow

### Creation

```
POST /api/workflows
{ "name": "...", "steps": [...] }
```

- New workflows are always created **inactive**
- User must explicitly activate via PATCH
