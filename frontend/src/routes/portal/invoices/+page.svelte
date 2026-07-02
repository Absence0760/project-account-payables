<script lang="ts">
	import { portalApi } from '$lib/portalApi';
	import { portalAuth } from '$lib/stores/portalAuth.svelte';
	import { onMount } from 'svelte';
	import { formatMoney } from '$lib/utils/money';
	import { formatDate } from '$lib/utils/time';
	import { m } from '$lib/i18n/store.svelte';
	import SupplierChatThread from '$lib/components/chat/SupplierChatThread.svelte';
	import type { PortalChatThread } from '$lib/types/supplierChat';
	import {
		getPortalChatThread,
		postPortalChatMessage,
		uploadPortalChatAttachment,
	} from '$lib/portalChat';

	interface PortalInvoice {
		id: string;
		invoice_number: string;
		amount: number | string;
		currency: string;
		status: string;
		invoice_date: string | null;
		due_date: string | null;
		submitted_at: string;
		file_url: string | null;
	}

	interface InvoiceListResponse {
		items: PortalInvoice[];
		total: number;
	}

	let items = $state<PortalInvoice[]>([]);
	let loading = $state(false);
	let uploading = $state(false);
	let error = $state('');
	let message = $state('');

	async function refresh() {
		loading = true;
		error = '';
		try {
			const res = await portalApi.get<InvoiceListResponse>('/api/portal/invoices');
			items = res.items;
		} catch (err) {
			error = err instanceof Error ? err.message : m('portal.invoices.loadFailed');
		} finally {
			loading = false;
		}
	}

	async function handleUpload(e: Event) {
		const input = e.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		uploading = true;
		error = '';
		message = '';
		try {
			await portalApi.upload<{ message: string }>('/api/portal/invoices', file);
			message = m('portal.invoices.submitted');
			input.value = '';
			await refresh();
		} catch (err) {
			error = err instanceof Error ? err.message : m('portal.invoices.uploadFailed');
		} finally {
			uploading = false;
		}
	}

	// --- Per-row expandable chat panel. Clicking a row toggles its thread;
	// the thread loads lazily on first expand and caches per invoice id. ---
	let expandedId = $state<string | null>(null);
	let chatThreads = $state<Record<string, PortalChatThread | null>>({});
	let chatLoading = $state<Record<string, boolean>>({});

	async function toggleChat(invoiceId: string) {
		if (expandedId === invoiceId) {
			expandedId = null;
			return;
		}
		expandedId = invoiceId;
		if (chatThreads[invoiceId] === undefined) {
			await loadChat(invoiceId);
		}
	}

	async function loadChat(invoiceId: string) {
		chatLoading = { ...chatLoading, [invoiceId]: true };
		try {
			chatThreads = { ...chatThreads, [invoiceId]: await getPortalChatThread(invoiceId) };
		} catch {
			chatThreads = {
				...chatThreads,
				[invoiceId]: { invoice_id: invoiceId, status: 'open', messages: [] },
			};
		} finally {
			chatLoading = { ...chatLoading, [invoiceId]: false };
		}
	}

	function chatSender(invoiceId: string) {
		return async (body: string, _mentions: string[], file?: File) => {
			if (file) {
				await uploadPortalChatAttachment(invoiceId, file, body || undefined);
			} else {
				await postPortalChatMessage(invoiceId, { body });
			}
			await loadChat(invoiceId);
		};
	}

	async function chatDownload(fileUrl: string, filename: string) {
		// `file_url` is "/api/portal/invoices/{id}/chat/file/{file_key}". The
		// download helper rebuilds that path from id + key, so pass the bytes
		// path straight through the portal client.
		const blob = await portalApi.download(fileUrl);
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = filename;
		a.click();
		URL.revokeObjectURL(url);
	}

	function fmtAmount(amount: number | string, ccy: string): string {
		return formatMoney(amount, { currency: ccy }, formatMoney(0, { currency: ccy }));
	}

	onMount(refresh);
</script>

<div class="page">
	<header>
		<h1>{m('portal.invoices.title')}</h1>
		<label class="upload-btn" class:uploading>
			<input type="file" accept="application/pdf,image/*" onchange={handleUpload} disabled={uploading} />
			{uploading ? m('portal.invoices.submitting') : m('portal.invoices.submit')}
		</label>
	</header>

	{#if error}<div class="error" role="alert">{error}</div>{/if}
	{#if message}<div class="msg" role="status" aria-live="polite">{message}</div>{/if}

	{#if loading && !items.length}
		<div class="loading">{m('portal.common.loading')}</div>
	{:else if !items.length}
		<div class="empty">
			<p>{m('portal.invoices.empty')}</p>
			<p class="hint">{m('portal.invoices.emptyHint')}</p>
		</div>
	{:else}
		<table>
			<thead>
				<tr>
					<th>{m('portal.invoices.col.invoiceNumber')}</th>
					<th>{m('portal.invoices.col.submitted')}</th>
					<th>{m('portal.invoices.col.invoiceDate')}</th>
					<th>{m('portal.invoices.col.due')}</th>
					<th class="num">{m('portal.invoices.col.amount')}</th>
					<th>{m('portal.invoices.col.status')}</th>
				</tr>
			</thead>
			<tbody>
				{#each items as inv}
					<tr
						class="clickable"
						class:expanded={expandedId === inv.id}
						onclick={(e) => {
							// Pointer enhancement: clicking anywhere on the row toggles the
							// chat disclosure, except when the click lands on the in-cell
							// toggle button (which handles it itself, incl. via keyboard).
							if ((e.target as HTMLElement).closest('.row-toggle')) return;
							toggleChat(inv.id);
						}}
					>
						<td>
							<button
								type="button"
								class="row-toggle"
								aria-expanded={expandedId === inv.id}
								onclick={() => toggleChat(inv.id)}
							>
								<span class="row-caret" aria-hidden="true"
									>{expandedId === inv.id ? '▾' : '▸'}</span
								>
								{inv.invoice_number || m('portal.invoices.pendingExtraction')}
							</button>
						</td>
						<td>{formatDate(inv.submitted_at, m('portal.common.dash'))}</td>
						<td>{formatDate(inv.invoice_date, m('portal.common.dash'))}</td>
						<td>{formatDate(inv.due_date, m('portal.common.dash'))}</td>
						<td class="num">{fmtAmount(inv.amount, inv.currency)}</td>
						<td><span class="status s-{inv.status}">{inv.status}</span></td>
					</tr>
					{#if expandedId === inv.id}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<tr class="chat-row" onclick={(e) => e.stopPropagation()}>
							<td colspan="6">
								<SupplierChatThread
									surface="vendor"
									thread={chatThreads[inv.id] ?? null}
									currentUserId={portalAuth.user?.id}
									members={[]}
									templates={[]}
									loading={chatLoading[inv.id] ?? false}
									onsend={chatSender(inv.id)}
									ondownload={chatDownload}
								/>
							</td>
						</tr>
					{/if}
				{/each}
			</tbody>
		</table>
	{/if}
</div>

<style>
	.page {
		max-width: 1100px;
		margin: 0 auto;
	}
	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 20px;
	}
	h1 {
		margin: 0;
		font-size: 1.25rem;
	}
	.upload-btn {
		display: inline-block;
		background: var(--accent);
		color: #fff;
		padding: 8px 14px;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.85rem;
	}
	.upload-btn.uploading {
		opacity: 0.6;
		cursor: default;
	}
	.upload-btn input {
		display: none;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 4px;
		overflow: hidden;
	}
	th,
	td {
		text-align: left;
		padding: 10px 12px;
		font-size: 0.88rem;
		border-bottom: 1px solid var(--border);
	}
	th {
		background: var(--bg);
		color: var(--text-muted);
		font-weight: 500;
		text-transform: uppercase;
		font-size: 0.72rem;
		letter-spacing: 0.04em;
	}
	tbody tr:last-child td {
		border-bottom: none;
	}
	tr.clickable {
		cursor: pointer;
	}
	tr.clickable:hover td {
		background: var(--bg);
	}
	tr.expanded td {
		background: var(--bg);
	}
	.row-toggle {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		background: none;
		border: none;
		padding: 0;
		margin: 0;
		font: inherit;
		color: inherit;
		cursor: pointer;
		text-align: left;
	}
	.row-caret {
		display: inline-block;
		width: 14px;
		color: var(--text-muted);
		font-size: 0.75rem;
	}
	tr.chat-row td {
		background: var(--bg);
		cursor: default;
		padding: 14px 16px;
	}
	.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.status {
		padding: 2px 8px;
		border-radius: 3px;
		font-size: 0.75rem;
		background: var(--bg);
		border: 1px solid var(--border);
	}
	.s-paid {
		background: rgba(40, 160, 80, 0.15);
		border-color: rgba(40, 160, 80, 0.4);
	}
	.s-rejected {
		background: rgba(224, 64, 64, 0.12);
		border-color: rgba(224, 64, 64, 0.35);
	}
	.empty,
	.loading {
		padding: 40px;
		text-align: center;
		background: var(--surface);
		border: 1px dashed var(--border);
		border-radius: 4px;
		color: var(--text-muted);
	}
	.empty .hint {
		font-size: 0.82rem;
	}
	.error {
		background: rgba(224, 64, 64, 0.1);
		border: 1px solid rgba(224, 64, 64, 0.3);
		color: #e04040;
		padding: 10px 14px;
		border-radius: 4px;
		margin-bottom: 12px;
	}
	.msg {
		background: rgba(40, 160, 80, 0.12);
		border: 1px solid rgba(40, 160, 80, 0.35);
		color: #2a9255;
		padding: 10px 14px;
		border-radius: 4px;
		margin-bottom: 12px;
	}
</style>
