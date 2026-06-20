// Types for A/B testing of workflow rules (workflow experiments).
// Money / statistic Decimals arrive as string-Decimal from the backend —
// display via `formatMoney` / render directly, never `parseFloat`.

export type ExperimentStatus = 'draft' | 'running' | 'concluded';

export type PrimaryMetric =
	| 'time_to_approval_days'
	| 'touchless_rate_pct'
	| 'exception_rate_pct'
	| 'rejection_rate_pct';

export const PRIMARY_METRIC_LABELS: Record<PrimaryMetric, string> = {
	time_to_approval_days: 'Time to approval (days)',
	touchless_rate_pct: 'Touchless rate',
	exception_rate_pct: 'Exception rate',
	rejection_rate_pct: 'Rejection rate'
};

export const STATUS_LABELS: Record<ExperimentStatus, string> = {
	draft: 'Draft',
	running: 'Running',
	concluded: 'Concluded'
};

export interface Experiment {
	id: string;
	name: string;
	description: string | null;
	workflow_definition_id: string;
	workflow_definition_name: string | null;
	config_a: Record<string, unknown>;
	config_b: Record<string, unknown>;
	split_a_pct: number;
	primary_metric: PrimaryMetric;
	min_sample_per_variant: number;
	status: ExperimentStatus;
	started_at: string | null;
	ended_at: string | null;
	assigned_count: number;
	entity_id: string | null;
	created_at: string | null;
	updated_at: string | null;
}

export interface ExperimentListResponse {
	experiments: Experiment[];
}

export interface ExperimentCreate {
	name: string;
	description?: string | null;
	workflow_definition_id: string;
	config_a: Record<string, unknown>;
	config_b: Record<string, unknown>;
	split_a_pct?: number;
	primary_metric?: PrimaryMetric;
	min_sample_per_variant?: number;
}

export interface VariantMetrics {
	variant: 'A' | 'B';
	assigned_count: number;
	completed_count: number;
	approved_count: number;
	rejected_count: number;
	touchless_count: number;
	exception_count: number;
	median_time_to_approval_days: string;
	avg_time_to_approval_days: string;
	touchless_rate_pct: string;
	exception_rate_pct: string;
	rejection_rate_pct: string;
}

export interface ExperimentResults {
	experiment_id: string;
	experiment_name: string;
	status: ExperimentStatus;
	primary_metric: PrimaryMetric;
	min_sample_per_variant: number;
	enough_data: boolean;
	winner: 'A' | 'B' | 'tie' | null;
	rationale: string;
	notes: string[];
	variant_a: VariantMetrics;
	variant_b: VariantMetrics;
	generated_at: string;
}
