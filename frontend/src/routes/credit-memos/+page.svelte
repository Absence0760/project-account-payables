<script lang="ts">
	import { api } from '$lib/api';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';

	const STATUS_CHIPS = [
		{ key: 'all', label: 'All' },
		{ key: 'open', label: 'Open' },
		{ key: 'applied', label: 'Applied' },
		{ key: 'void', label: 'Void' }
	];

	const COLUMNS = [
		{ label: 'Memo #' },
		{ label: 'Vendor' },
		{ label: 'Amount', class: 'right' },
		{ label: 'Issued' },
		{ label: 'Applied To' },
		{ label: 'Status' },
		{ class: 'actions-col' }
	];

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

<PageHeader title="Credit Memos">
	{#snippet actions()}
		<button class="btn-primary" onclick={() => (showCreate = true)}>+ New Credit Memo</button>
	{/snippet}

	<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />

	<DataTable
		columns={COLUMNS}
		isEmpty={memos.length === 0}
		empty={loading ? 'Loading…' : 'No credit memos.'}
	>
		{#snippet body()}
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
			{/each}
		{/snippet}
	</DataTable>

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
</PageHeader>

<Modal
	open={showCreate}
	ariaLabel="New credit memo"
	title="New Credit Memo"
	width="sm"
	onclose={() => (showCreate = false)}
>
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
</Modal>

<Modal
	open={applyTargetId !== null}
	ariaLabel="Apply credit memo"
	title="Apply Credit Memo"
	width="sm"
	onclose={() => (applyTargetId = null)}
>
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
</Modal>

<style>
	/* Page-specific bits not covered by the global design-system CSS in app.css. */
	tr.applied td,
	tr.void td {
		opacity: 0.6;
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
</style>
