// Types for the SOX records-management retention-policy surface
// (`/admin/retention`). Mirrors `backend/app/api/retention.py`'s
// `RetentionPolicyResponse` / `UpdateRetentionPolicyRequest`.

/** Record classes the retention engine understands (`RECORD_CLASSES`). Keep in
 *  sync with the backend — an unknown class 422s the PUT before any write. */
export const RETENTION_RECORD_CLASSES = ['invoices', 'audit_log'] as const;
export type RetentionRecordClass = (typeof RETENTION_RECORD_CLASSES)[number];

export interface RetentionPolicyResponse {
	/** Effective window per class, in months (per-org override → platform default). */
	policy: Record<string, number>;
	/** The platform default, for "(default)" affordances. */
	default_months: number;
	/** Whether the enforcement sweep is running (env-gated; not editable here). */
	enabled: boolean;
}

export interface UpdateRetentionPolicyRequest {
	/** Only the classes present are updated; omitted classes keep their current value. */
	policy: Record<string, number>;
}
