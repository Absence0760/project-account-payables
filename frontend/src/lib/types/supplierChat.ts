// Types for the Embedded Supplier Chat & Collaboration feature. These mirror
// the §9 JSON handshake in reviews/supplier-chat-contract.md exactly. The AP
// surface (full) and the masked portal surface (Portal* variants) differ: the
// portal never sees an internal `users.id` (no `author_user_id`) and never
// carries mentions.

export type ChatAuthorRole = 'ap_team' | 'supplier' | 'system';
export type ChatThreadStatus = 'open' | 'resolved';

export interface ChatAttachment {
	file_url: string;
	filename: string;
	content_type: string;
	size: number;
}

export interface ChatMessage {
	id: string;
	thread_id: string;
	author_role: ChatAuthorRole;
	author_user_id: string | null;
	author_name: string | null;
	body: string;
	mention_user_ids: string[];
	template_key: string | null;
	attachments: ChatAttachment[];
	created_at: string; // ISO 8601 (AP schemas serialize with .isoformat())
}

export interface ChatThread {
	id: string | null; // null when not yet lazy-created
	invoice_id: string;
	status: ChatThreadStatus;
	resolved_at: string | null;
	resolved_by: string | null;
	messages: ChatMessage[];
}

export interface ChatTemplate {
	key: string; // "missing_po" | "amount_mismatch" | "payment_status"
	label: string;
	body: string;
}

// Portal variants — masked: no `author_user_id`, no mentions, no template_key.
export interface PortalChatMessage {
	id: string;
	author_role: ChatAuthorRole;
	author_name: string | null;
	body: string;
	attachments: ChatAttachment[];
	created_at: string; // portal schemas use raw datetime → ISO string on the wire
}

export interface PortalChatThread {
	invoice_id: string;
	status: ChatThreadStatus;
	messages: PortalChatMessage[];
}
