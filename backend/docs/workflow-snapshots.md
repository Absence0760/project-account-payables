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
