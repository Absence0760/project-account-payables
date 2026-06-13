// Typed helpers for the AP-side supplier-chat endpoints (under /api/invoices/…).
// All requests route through the shared `api` client (Bearer + X-Tenant-Slug +
// 401-bounce), mirroring $lib/api/audit.ts.
import { api } from '$lib/api';
import type { ChatMessage, ChatThread, ChatTemplate } from '$lib/types/supplierChat';

export function getChatThread(invoiceId: string): Promise<ChatThread> {
	return api.get<ChatThread>(`/api/invoices/${invoiceId}/chat`);
}

export function postChatMessage(
	invoiceId: string,
	body: { body: string; mention_user_ids?: string[]; template_key?: string },
): Promise<ChatMessage> {
	return api.post<ChatMessage>(`/api/invoices/${invoiceId}/chat`, body);
}

export function uploadChatAttachment(
	invoiceId: string,
	file: File,
	body?: string,
	mentionUserIds?: string[],
): Promise<ChatMessage> {
	return api.upload<ChatMessage>(`/api/invoices/${invoiceId}/chat/attachments`, file, {
		body,
		mention_user_ids: mentionUserIds,
	});
}

export function resolveChatThread(invoiceId: string): Promise<ChatThread> {
	return api.post<ChatThread>(`/api/invoices/${invoiceId}/chat/resolve`, {});
}

export function reopenChatThread(invoiceId: string): Promise<ChatThread> {
	return api.post<ChatThread>(`/api/invoices/${invoiceId}/chat/reopen`, {});
}

export function getChatTemplates(): Promise<ChatTemplate[]> {
	return api.get<ChatTemplate[]>('/api/invoices/chat/templates');
}

// Path for an attachment's bytes — feed to `api.fetchBlob` for inline render.
export function chatAttachmentUrl(invoiceId: string, fileKey: string): string {
	return `/api/invoices/${invoiceId}/chat/file/${fileKey}`;
}
