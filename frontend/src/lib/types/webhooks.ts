// Types for the admin outbound-webhook management surface (`/admin/webhooks`).
// Mirrors the backend `SubscriptionResponse` / `SubscriptionCreatedResponse` /
// `DeliveryResponse` shapes in `backend/app/api/webhooks.py`. The full HMAC
// `signing_secret` is ONLY ever present on the create response and is shown
// exactly once — it is never stored, echoed, or re-fetchable (list/get carry
// only `secret_prefix`).

/** A subscribable platform event type (mirrors `WEBHOOK_EVENT_TYPES`). */
export type WebhookEventType = 'invoice.approved' | 'payment.settled' | 'exception.raised';

/** All event types the create/edit forms offer, in display order. */
export const WEBHOOK_EVENT_TYPES: WebhookEventType[] = [
	'invoice.approved',
	'payment.settled',
	'exception.raised'
];

export interface WebhookSubscription {
	id: string;
	name: string;
	target_url: string;
	event_types: string[];
	secret_prefix: string;
	active: boolean;
	created_at: string | null;
	updated_at: string | null;
}

/** The create response — the ONLY place the full signing secret is returned. */
export interface WebhookSubscriptionCreated {
	subscription: WebhookSubscription;
	// Shown once; copy it now. Never persisted client-side after the modal closes.
	signing_secret: string;
}

/** Delivery lifecycle states (mirrors `DELIVERY_*` constants). */
export type WebhookDeliveryStatus = 'pending' | 'delivered' | 'failed' | 'dead';

export interface WebhookDelivery {
	id: string;
	subscription_id: string;
	event_id: string;
	event_type: string;
	status: string;
	attempt_count: number;
	response_code: number | null;
	next_attempt_at: string | null;
	last_attempt_at: string | null;
	created_at: string | null;
}
