<script lang="ts">
	import { api } from '$lib/api';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';

	interface CreditMemo {
		id: string;
		memo_number: string;
		vendor_id: string;
		vendor_name: string | null;
		invoice_id: string | null;
		invoice_number: string | null;
		amount: number;
		currency: string;
		issued_date: string | null;
		reason: string | null;
		status: string;
		applied_at: string | null;
		applied_by: string | null;
		created_at: string;
	}

	interface Vendor {
		id: string;
		name: string;
	}

	interface Invoice {
		id: string;
		invoice_number: string;
		vendor: string;
		vendor_id: string | null;
	}

	let memos = $state<CreditMemo[]>([]);
	let vendors = $state<Vendor[]>([]);
	let invoices = $state<Invoice[]>([]);
	let loading = $state(true);
	let statusFilter = $state<string>('all');
	let showCreate = $state(false);
	let applyTargetId = $state<string | null>(null);

	const PAGE_SIZE = 20;
	let total = $state(0);
	let page = $state(1);
	let loadingMore = $state(false);

	let newMemoNumber = $state('');
	let newVendorId = $state('');
	let newAmount = $state('');
	let newReason = $state('');
	let saving = $state(false);

	let applyInvoiceId = $state('');
	let applying = $state(false);

	$effect(() => {
		loadAll();
	});

	$effect(() => {
		statusFilter;
		loadMemos();
	});

	async function loadAll() {
		loading = true;
		await Promise.all([loadMemos(), loadVendors(), loadInvoices()]);
		loading = false;
	}

	async function loadMemos(opts: { append?: boolean; nextPage?: number } = {}) {
		try {
			const nextPage = opts.nextPage ?? 1;
			const params = new URLSearchParams();
			if (statusFilter !== 'all') params.set('status', statusFilter);
			params.set('page', String(nextPage));
			params.set('page_size', String(PAGE_SIZE));
			const data = await api.get<{ items: CreditMemo[]; total: number }>(
				`/api/credit-memos?${params}`
			);
			memos = opts.append ? [...memos, ...data.items] : data.items;
			total = data.total;
			page = nextPage;
		} catch {
			toast('Failed to load credit memos', 'error');
		}
	}

	async function loadMoreMemos() {
		loadingMore = true;
		try {
			await loadMemos({ append: true, nextPage: page + 1 });
		} finally {
			loadingMore = false;
		}
	}

	let hasMore = $derived(memos.length < total);

	async function loadVendors() {
		try {
			const data = await api.get<{ items: Vendor[] }>('/api/vendors');
			vendors = data.items;
		} catch {
			/* non-critical for the list view */
		}
	}

	async function loadInvoices() {
		try {
			const data = await api.get<{ items: Invoice[] }>('/api/invoices');
			invoices = data.items;
		} catch {
			/* non-critical */
		}
	}

	async function handleCreate() {
		if (!newMemoNumber.trim() || !newVendorId || !newAmount) return;
		saving = true;
		try {
			await api.post('/api/credit-memos', {
				memo_number: newMemoNumber.trim(),
				vendor_id: newVendorId,
				amount: parseFloat(newAmount),
				reason: newReason.trim() || null
			});
			toast('Credit memo created', 'success');
			showCreate = false;
			newMemoNumber = '';
			newVendorId = '';
			newAmount = '';
			newReason = '';
			await loadMemos();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Create failed', 'error');
		} finally {
			saving = false;
		}
	}

	async function handleApply() {
		if (!applyTargetId || !applyInvoiceId) return;
		applying = true;
		try {
			await api.post(`/api/credit-memos/${applyTargetId}/apply`, {
				invoice_id: applyInvoiceId
			});
			toast('Credit memo applied', 'success');
			applyTargetId = null;
			applyInvoiceId = '';
			await loadMemos();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Apply failed', 'error');
		} finally {
			applying = false;
		}
	}

	async function handleVoid(id: string) {
		try {
			await api.post(`/api/credit-memos/${id}/void`, {});
			toast('Credit memo voided', 'success');
			await loadMemos();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Void failed', 'error');
		}
	}

	function formatCurrency(amount: number, currency: string = 'USD'): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount);
	}

	function formatDate(s: string | null): string {
		if (!s) return '—';
		return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
	}

	let invoicesForVendor = $derived.by(() => {
		const memo = memos.find((m) => m.id === applyTargetId);
		if (!memo) return invoices;
		return invoices.filter((i) => !i.vendor_id || i.vendor_id === memo.vendor_id);
	});
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Credit Memos</h1>
		<button class="btn-primary" onclick={() => (showCreate = true)}>+ New Credit Memo</button>
	</header>

	<div class="filter-row">
		<nav class="filters">
			<button class="filter-chip" class:active={statusFilter === 'all'} onclick={() => (statusFilter = 'all')}>All</button>
			<button class="filter-chip" class:active={statusFilter === 'open'} onclick={() => (statusFilter = 'open')}>Open</button>
			<button class="filter-chip" class:active={statusFilter === 'applied'} onclick={() => (statusFilter = 'applied')}>Applied</button>
			<button class="filter-chip" class:active={statusFilter === 'void'} onclick={() => (statusFilter = 'void')}>Void</button>
		</nav>
	</div>

	<div class="grid-container">
		<table>
			<thead>
				<tr>
					<th>Memo #</th>
					<th>Vendor</th>
					<th class="right">Amount</th>
					<th>Issued</th>
					<th>Applied To</th>
					<th>Status</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each memos as memo (memo.id)}
					<tr class:applied={memo.status === 'applied'} class:void={memo.status === 'void'}>
						<td class="mono">{memo.memo_number}</td>
						<td>{memo.vendor_name ?? '—'}</td>
						<td class="right mono">{formatCurrency(memo.amount, memo.currency)}</td>
						<td class="muted">{formatDate(memo.issued_date)}</td>
						<td class="mono muted">{memo.invoice_number ?? '—'}</td>
						<td><span class="badge {memo.status}">{memo.status}</span></td>
						<td class="actions">
							{#if memo.status === 'open'}
								<RowAction onclick={() => { applyTargetId = memo.id; applyInvoiceId = ''; }}>Apply</RowAction>
								<RowAction variant="danger" onclick={() => handleVoid(memo.id)}>Void</RowAction>
							{/if}
						</td>
					</tr>
				{:else}
					<tr><td colspan="7" class="empty">{loading ? 'Loading…' : 'No credit memos.'}</td></tr>
				{/each}
			</tbody>
		</table>
	</div>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMoreMemos} disabled={loadingMore}>
				{loadingMore ? 'Loading…' : `Load more (${memos.length} of ${total})`}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">Showing all {total} credit memo{total === 1 ? '' : 's'}</span>
		</div>
	{/if}
</div>

{#if showCreate}
	<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) showCreate = false; }}>
		<div class="modal" role="dialog" aria-label="New credit memo">
			<h2>New Credit Memo</h2>
			<form onsubmit={(e) => { e.preventDefault(); handleCreate(); }}>
				<label>
					<span>Memo Number <em class="required">*</em></span>
					<input type="text" bind:value={newMemoNumber} required />
				</label>
				<label>
					<span>Vendor <em class="required">*</em></span>
					<select bind:value={newVendorId} required>
						<option value="">Select vendor…</option>
						{#each vendors as v}
							<option value={v.id}>{v.name}</option>
						{/each}
					</select>
				</label>
				<label>
					<span>Amount <em class="required">*</em></span>
					<input type="number" min="0.01" step="0.01" bind:value={newAmount} required />
				</label>
				<label>
					<span>Reason</span>
					<textarea bind:value={newReason} rows="2" placeholder="e.g. Returned defective goods"></textarea>
				</label>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={() => (showCreate = false)}>Cancel</button>
					<button type="submit" class="btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Create'}</button>
				</div>
			</form>
		</div>
	</div>
{/if}

{#if applyTargetId}
	<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) applyTargetId = null; }}>
		<div class="modal" role="dialog" aria-label="Apply credit memo">
			<h2>Apply Credit Memo</h2>
			<p class="modal-hint">Pick an invoice to apply this credit to.</p>
			<form onsubmit={(e) => { e.preventDefault(); handleApply(); }}>
				<label>
					<span>Invoice <em class="required">*</em></span>
					<select bind:value={applyInvoiceId} required>
						<option value="">Select invoice…</option>
						{#each invoicesForVendor as inv}
							<option value={inv.id}>{inv.invoice_number} — {inv.vendor}</option>
						{/each}
					</select>
				</label>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={() => (applyTargetId = null)}>Cancel</button>
					<button type="submit" class="btn-primary" disabled={applying}>{applying ? 'Applying…' : 'Apply'}</button>
				</div>
			</form>
		</div>
	</div>
{/if}

<style>
	.workspace {
		max-width: 1800px;
		margin: 0 auto;
		padding: 24px 20px;
		display: flex;
		flex-direction: column;
		gap: 16px;
		min-height: 100vh;
	}
	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	h1 {
		font-size: 1.3rem;
		font-weight: 700;
		margin: 0;
	}
	.btn-primary {
		padding: 8px 18px;
		border-radius: 6px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-primary:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	.filters {
		display: flex;
		align-items: center;
		gap: 6px;
		flex-wrap: wrap;
	}
	.filter-chip {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 6px 14px;
		border-radius: 20px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.15s;
		font-family: inherit;
	}
	.filter-chip:hover {
		border-color: var(--accent);
		color: var(--text);
	}
	.filter-chip.active {
		background: var(--accent);
		color: #fff;
		border-color: var(--accent);
	}
	.grid-container {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.875rem;
	}
	th {
		background: var(--bg);
		text-align: left;
		padding: 10px 14px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
	}
	td {
		padding: 10px 14px;
		border-bottom: 1px solid var(--border);
	}
	tr.applied td,
	tr.void td {
		opacity: 0.6;
	}
	.mono {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.82rem;
	}
	.right {
		text-align: right;
	}
	.muted {
		color: var(--text-muted);
	}
	.empty {
		text-align: center;
		padding: 40px;
		color: var(--text-muted);
	}
	.badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.74rem;
		font-weight: 600;
		text-transform: capitalize;
	}
	.badge.open {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}
	.badge.applied {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}
	.badge.void {
		background: var(--bg);
		color: var(--text-muted);
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
	}
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: grid;
		place-items: center;
		z-index: 100;
		backdrop-filter: blur(2px);
	}
	.modal {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		width: min(440px, 92vw);
		padding: 24px;
	}
	.modal h2 {
		margin: 0 0 4px;
		font-size: 1.1rem;
		font-weight: 600;
	}
	.modal-hint {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0 0 16px;
	}
	.modal form {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.modal label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.modal label span {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.modal input,
	.modal select,
	.modal textarea {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.88rem;
		color: var(--text);
		font-family: inherit;
	}
	.modal-footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding-top: 8px;
		border-top: 1px solid var(--border);
	}
	.btn-cancel {
		padding: 8px 18px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
	}
	.required {
		color: #e04040;
		font-style: normal;
	}
	.load-more-row {
		display: flex;
		justify-content: center;
		padding: 8px 0 4px;
	}
	.btn-load-more {
		padding: 8px 18px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-load-more:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.btn-load-more:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.load-more-end {
		font-size: 0.78rem;
		color: var(--text-muted);
	}
</style>
