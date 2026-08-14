// Mirrors the statuses the backend actually persists on `payments.status`.
// `pending_compliance` is the parking state the sanctions/KYC gate
// (`services/compliance.check_payment_compliance`) leaves a payment in — it
// must be listed here or the History badge renders blank (no label) and the
// status has no filter chip, which is how a held payment stayed invisible.
// Its two exits are `POST /api/payments/{id}/compliance/{release,dismiss}`.
export type PaymentStatus =
	| 'pending'
	| 'pending_compliance'
	| 'submitted'
	| 'processing'
	| 'completed'
	| 'failed'
	| 'cancelled'
	| 'voided';

export const PAYMENT_STATUSES: PaymentStatus[] = [
	'pending',
	'pending_compliance',
	'submitted',
	'processing',
	'completed',
	'failed',
	'cancelled',
	'voided'
];

export const PAYMENT_STATUS_LABELS: Record<PaymentStatus, string> = {
	pending: 'Pending',
	pending_compliance: 'Compliance Hold',
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
	card_last_four: string | null;
	card_provider: string | null;
	card_id: string | null;
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
