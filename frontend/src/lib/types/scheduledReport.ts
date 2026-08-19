/**
 * Types for the scheduled-report admin surface (`/cfo` → Scheduled Reports).
 *
 * Mirrors the backend `ScheduledReportResponse` / list envelope under
 * `/api/analytics/scheduled-reports`. The runner
 * (`backend/app/services/scheduled_reports.py`) shipped complete and this CRUD
 * surface did not, so a schedule could only be inserted by hand-written SQL and
 * `list_due_schedules` returned `[]` on every tick forever.
 *
 * Two deliberate shape choices:
 *
 *  - `report_type` and `cadence` are **plain strings, not unions**. Both
 *    vocabularies come off the runner's own registries and ride the list
 *    envelope (`report_types` / `cadences`); a TypeScript union here would turn
 *    "the backend gained a report type" into a compile error on an unchanged
 *    frontend and — worse — invite a hardcoded copy of the list. The UI drives
 *    both selects off the response and humanises an unknown key rather than
 *    hiding it (see `components/analytics/scheduledReportDisplay.ts`).
 *  - `last_run_status` IS a union: it is a fixed lifecycle vocabulary the UI
 *    branches on (a `failure` renders differently from a `partial`), so a new
 *    member is a real UI decision, not a data value to pass through.
 *
 * There is no `entity_id` and no entity selector anywhere on this surface:
 * `X-Entity-ID` is not honoured by the scheduled-report routes — a schedule is
 * whole-tenant by construction.
 */

/** Outcome of the runner's last attempt. `null` = never run yet. */
export type ScheduledReportRunStatus = 'success' | 'partial' | 'failure';

export interface ScheduledReport {
	id: string;
	name: string;
	/** A key from the list envelope's `report_types` — never a closed union. */
	report_type: string;
	/** A key from the list envelope's `cadences` — never a closed union. */
	cadence: string;
	/** De-duped case-insensitively by the backend, so this is the list that was
	 *  actually SAVED — which is not necessarily the list that was submitted. */
	recipients: string[];
	period_days: number;
	enabled: boolean;
	next_run_at: string;
	last_run_at: string | null;
	last_run_status: ScheduledReportRunStatus | null;
	/** Counts + an exception class only — never a recipient address. Shown
	 *  verbatim for `partial` (some recipients DID receive it) and `failure`. */
	last_run_error: string | null;
}

/** `GET /api/analytics/scheduled-reports` — the rows plus the two vocabularies
 *  that drive the create / edit selects. */
export interface ScheduledReportList {
	schedules: ScheduledReport[];
	report_types: string[];
	cadences: string[];
}

/** `POST` body. `period_days` / `enabled` / `next_run_at` are optional; an
 *  omitted `next_run_at` means "now", i.e. it fires on the next runner tick.
 *  Pass an explicit value to pin a time-of-day (a past value is legitimate). */
export interface ScheduledReportCreate {
	name: string;
	report_type: string;
	cadence: string;
	recipients: string[];
	period_days?: number;
	enabled?: boolean;
	next_run_at?: string;
}

/** `PATCH` body — any subset; only what is sent changes. */
export type ScheduledReportPatch = Partial<ScheduledReportCreate>;
