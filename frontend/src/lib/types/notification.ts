export type NotificationEventType =
	| 'invoice_assigned'
	| 'invoice_approved'
	| 'invoice_rejected'
	| 'invoice_paid';

export interface Notification {
	id: string;
	event_type: NotificationEventType;
	entity_type: string;
	entity_id: string | null;
	title: string;
	body: string | null;
	read_at: string | null;
	created_at: string;
}

export interface NotificationListResponse {
	items: Notification[];
	total: number;
	unread: number;
	page: number;
	page_size: number;
}

export interface ChannelPrefs {
	email: boolean;
	in_app: boolean;
}

export interface NotificationPrefs {
	invoice_assigned: ChannelPrefs;
	invoice_approved: ChannelPrefs;
	invoice_rejected: ChannelPrefs;
	invoice_paid: ChannelPrefs;
}

/** Display labels for each event type (used in the center + preferences). */
export const EVENT_LABELS: Record<NotificationEventType, string> = {
	invoice_assigned: 'Invoice assigned to me',
	invoice_approved: 'Invoice approved',
	invoice_rejected: 'Invoice rejected',
	invoice_paid: 'Invoice paid',
};

export const EVENT_ORDER: NotificationEventType[] = [
	'invoice_assigned',
	'invoice_approved',
	'invoice_rejected',
	'invoice_paid',
];
