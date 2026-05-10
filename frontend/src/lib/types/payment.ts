export type PaymentStatus =
	| 'pending'
	| 'submitted'
	| 'processing'
	| 'completed'
	| 'failed'
	| 'cancelled'
	| 'voided';

export const PAYMENT_STATUSES: PaymentStatus[] = [
	'pending',
	'submitted',
	'processing',
	'completed',
	'failed',
	'cancelled',
	'voided'
];

export const PAYMENT_STATUS_LABELS: Record<PaymentStatus, string> = {
	pending: 'Pending',
	submitted: 'Submitted',
	processing: 'Processing',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled',
	voided: 'Voided'
};

export type PaymentMethod = 'ach' | 'wire' | 'check' | 'virtual_card';

export const PAYMENT_METHODS: PaymentMethod[] = ['ach', 'wire', 'check', 'virtual_card'];

export const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = {
	ach: 'ACH',
	wire: 'Wire',
	check: 'Check',
	virtual_card: 'Virtual Card'
};

export interface Payment {
	id: string;
	correlation_id: string | null;
	invoice_id: string;
	payment_run_id: string | null;
	amount: number;
	method: PaymentMethod | null;
	status: PaymentStatus;
	reference: string | null;
	created_at: string;
	updated_at: string | null;
	vendor_name: string | null;
	invoice_number: string | null;
}

export interface PaymentRun {
	id: string;
	status: string;
	total_amount: number | null;
	initiated_by: string | null;
	executed_at: string | null;
	created_at: string;
	payment_count: number;
}
