// Audit-trail types shared by the invoice modal and the auditor console.

// One field's before/after values in a SOX change-history diff. Money values
// arrive as string-Decimal from the backend (never a JS number), so the type
// is intentionally `unknown` rather than `number | string`.
export interface AuditFieldChange {
	old: unknown;
	new: unknown;
}

export interface AuditEntry {
	id: string;
	correlation_id?: string;
	actor_id?: string | null;
	actor_name: string | null;
	actor_email?: string | null;
	action: string;
	entity_type?: string;
	entity_id?: string | null;
	// `details.changes` carries the per-field before/after diff for edit /
	// approve-with-corrections events; `details.fields` lists the field-NAMES a
	// view-event touched (never the values).
	details:
		| (Record<string, unknown> & { changes?: Record<string, AuditFieldChange> })
		| null;
	created_at: string;
}
