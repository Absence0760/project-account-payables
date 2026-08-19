<script lang="ts">
	import { api } from '$lib/api';
	import { appendUnique } from '$lib/utils/pagination';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import { untrack } from 'svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import { formatDate } from '$lib/utils/time';

	const STATUS_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		{ key: 'open', label: m('creditMemos.status.open') },
		{ key: 'applied', label: m('creditMemos.status.applied') },
		{ key: 'void', label: m('creditMemos.status.void') }
	]);

	const COLUMNS = $derived([
		{ label: m('creditMemos.col.memoNumber') },
		{ label: m('creditMemos.col.vendor') },
		{ label: m('creditMemos.col.amount'), class: 'right' },
		{ label: m('creditMemos.col.issued') },
		{ label: m('creditMemos.col.appliedTo') },
		{ label: m('creditMemos.col.status') },
		{ class: 'actions-col' }
	]);

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

	// Sequences every `loadMemos` call — mount, status chip, load-more; one
	// counter, latest-issued wins — so a page-1 replace and a page-2 append
	// can't land out of order. Load more, then switch the chip: the replace
	// landed first and the append then pushed the OLD filter's page-2 rows onto
	// the new list and overwrote `total`/`page` with them. Create / apply / void
	// all re-fetch through this loader rather than editing a row in place, so no
	// `supersedeInFlight()` call is needed. See `frontend/CLAUDE.md`
	// § Sequencing list fetches.
	const fetchSequence = createRequestSequencer();

	// The mount effect loads all three lists ONCE. It must not depend on
	// `statusFilter`: `loadMemos` reads it synchronously (before its first
	// await), and Svelte tracks reads transitively through called functions, so
	// a plain read there made this effect a second status-filter subscriber —
	// every chip click fired it AND the effect below, two unsequenced page-1
	// requests racing with whichever landed last winning (and the vendor /
	// invoice selects needlessly refetched). `untrack` inside `loadMemos` still
	// reads the CURRENT filter, it just stops the read registering as the
	// caller's dependency.
	$effect(() => {
		loadAll();
	});

	// Status chip → refetch. Skips its own mount-time run: a Svelte `$effect`
	// always fires once immediately whether or not its tracked value actually
	// changed, so without the guard this queued a second page-1 request on top
	// of the mount load above.
	let statusEffectRan = false;
	$effect(() => {
		statusFilter;
		if (!statusEffectRan) {
			statusEffectRan = true;
			return;
		}
		loadMemos();
	});

	async function loadAll() {
		await Promise.all([loadMemos(), loadVendors(), loadInvoices()]);
	}

	async function loadMemos(opts: { append?: boolean; nextPage?: number } = {}) {
		const token = fetchSequence.start();
		// `loading` is what the table renders instead of "No credit memos" while
		// a fetch is out. `loadMemos` never touched it, so a status-chip change
		// sat on the previous filter's rows with no spinner until the response
		// landed.
		if (opts.append) loadingMore = true;
		else loading = true;
		try {
			const nextPage = opts.nextPage ?? 1;
			const params = new URLSearchParams();
			const status = untrack(() => statusFilter);
			if (status !== 'all') params.set('status', status);
			params.set('page', String(nextPage));
			params.set('page_size', String(PAGE_SIZE));
			const data = await api.get<{ items: CreditMemo[]; total: number }>(
				`/api/credit-memos?${params}`
			);
			// Superseded by a newer load — discard rather than clobber.
			if (!fetchSequence.canCommit(token)) return;
			memos = opts.append ? appendUnique(memos, data.items) : data.items;
			total = data.total;
			page = nextPage;
		} catch {
			// `isCurrentRequest`, not `canCommit`: only the newest request reports.
			if (!fetchSequence.isCurrentRequest(token)) return;
			toast(m('creditMemos.toast.loadFailed'), 'error');
		} finally {
			if (fetchSequence.isCurrentRequest(token)) {
				loading = false;
				loadingMore = false;
			}
		}
	}

	async function loadMoreMemos() {
		await loadMemos({ append: true, nextPage: page + 1 });
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
			toast(m('creditMemos.toast.created'), 'success');
			showCreate = false;
			newMemoNumber = '';
			newVendorId = '';
			newAmount = '';
			newReason = '';
			await loadMemos();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('creditMemos.toast.createFailed'), 'error');
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
			toast(m('creditMemos.toast.applied'), 'success');
			applyTargetId = null;
			applyInvoiceId = '';
			await loadMemos();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('creditMemos.toast.applyFailed'), 'error');
		} finally {
			applying = false;
		}
	}

	// Void is irreversible — arm on first click, commit on the second (the
	// app-wide destructive-action pattern), and guard against a double-submit.
	let confirmVoidId = $state<string | null>(null);
	let voidingId = $state<string | null>(null);

	async function handleVoid(id: string) {
		if (confirmVoidId !== id) {
			confirmVoidId = id;
			return;
		}
		confirmVoidId = null;
		voidingId = id;
		try {
			await api.post(`/api/credit-memos/${id}/void`, {});
			toast(m('creditMemos.toast.voided'), 'success');
			await loadMemos();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('creditMemos.toast.voidFailed'), 'error');
		} finally {
			voidingId = null;
		}
	}

	// Only this memo's own vendor's invoices are valid targets. An invoice with
	// no resolved `vendor_id` is NOT a wildcard — its vendor can't be proven, so
	// the backend refuses the apply (409) and offering it here would only invite
	// the error. Resolve the invoice's vendor first (re-save it on the invoice).
	let invoicesForVendor = $derived.by(() => {
		const memo = memos.find((m) => m.id === applyTargetId);
		if (!memo) return [];
		return invoices.filter((i) => i.vendor_id === memo.vendor_id);
	});
</script>

<svelte:window
	onclick={(e) => {
		// Outside-click un-arms a pending Void confirmation.
		if (confirmVoidId && !(e.target as HTMLElement)?.closest?.('.row-action')) {
			confirmVoidId = null;
		}
	}}
/>

<PageHeader title={m('creditMemos.title')}>
	{#snippet actions()}
		<button class="btn-primary" onclick={() => (showCreate = true)}>{m('creditMemos.new')}</button>
	{/snippet}

	<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />

	<DataTable
		columns={COLUMNS}
		isEmpty={memos.length === 0}
		empty={loading ? m('common.loading') : m('creditMemos.empty')}
	>
		{#snippet body()}
			{#each memos as memo (memo.id)}
				<tr class:applied={memo.status === 'applied'} class:void={memo.status === 'void'}>
					<td class="mono">{memo.memo_number}</td>
					<td>{memo.vendor_name ?? '—'}</td>
					<td class="right mono"><Money amount={memo.amount} currency={memo.currency} /></td>
					<td class="muted">{formatDate(memo.issued_date)}</td>
					<td class="mono muted">{memo.invoice_number ?? '—'}</td>
					<td><span class="badge {memo.status}">{memo.status}</span></td>
					<td class="actions">
						{#if memo.status === 'open'}
							<RowAction onclick={() => { applyTargetId = memo.id; applyInvoiceId = ''; }}>{m('creditMemos.row.apply')}</RowAction>
							<RowAction
								variant="danger"
								armed={confirmVoidId === memo.id}
								disabled={voidingId === memo.id}
								onclick={() => handleVoid(memo.id)}
							>
								{confirmVoidId === memo.id ? m('creditMemos.row.confirm') : m('creditMemos.row.void')}
							</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMoreMemos} disabled={loadingMore}>
				{loadingMore ? m('common.loading') : m('creditMemos.loadMore', { shown: memos.length, total })}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('creditMemos.showingAll', { total })}</span>
		</div>
	{/if}
</PageHeader>

<Modal
	open={showCreate}
	ariaLabel={m('creditMemos.createModal.aria')}
	title={m('creditMemos.createModal.title')}
	width="sm"
	onclose={() => (showCreate = false)}
>
	<form onsubmit={(e) => { e.preventDefault(); handleCreate(); }}>
		<label>
			<span>{m('creditMemos.createModal.memoNumber')} <em class="required">*</em></span>
			<input type="text" bind:value={newMemoNumber} required />
		</label>
		<label>
			<span>{m('creditMemos.createModal.vendor')} <em class="required">*</em></span>
			<select bind:value={newVendorId} required>
				<option value="">{m('creditMemos.createModal.selectVendor')}</option>
				{#each vendors as v}
					<option value={v.id}>{v.name}</option>
				{/each}
			</select>
		</label>
		<label>
			<span>{m('creditMemos.createModal.amount')} <em class="required">*</em></span>
			<input type="number" min="0.01" step="0.01" bind:value={newAmount} required />
		</label>
		<label>
			<span>{m('creditMemos.createModal.reason')}</span>
			<textarea bind:value={newReason} rows="2" placeholder={m('creditMemos.createModal.reasonPlaceholder')}></textarea>
		</label>
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (showCreate = false)}>{m('common.cancel')}</button>
			<button type="submit" class="btn-primary" disabled={saving}>{saving ? m('common.saving') : m('creditMemos.createModal.create')}</button>
		</div>
	</form>
</Modal>

<Modal
	open={applyTargetId !== null}
	ariaLabel={m('creditMemos.applyModal.aria')}
	title={m('creditMemos.applyModal.title')}
	width="sm"
	onclose={() => (applyTargetId = null)}
>
	<p class="modal-hint">{m('creditMemos.applyModal.hint')}</p>
	<form onsubmit={(e) => { e.preventDefault(); handleApply(); }}>
		<label>
			<span>{m('creditMemos.applyModal.invoice')} <em class="required">*</em></span>
			<select bind:value={applyInvoiceId} required>
				<option value="">{m('creditMemos.applyModal.selectInvoice')}</option>
				{#each invoicesForVendor as inv}
					<option value={inv.id}>{inv.invoice_number} — {inv.vendor}</option>
				{/each}
			</select>
		</label>
		{#if invoicesForVendor.length === 0}
			<p class="modal-hint warn">{m('creditMemos.applyModal.noEligible')}</p>
		{/if}
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (applyTargetId = null)}>{m('common.cancel')}</button>
			<button type="submit" class="btn-primary" disabled={applying || invoicesForVendor.length === 0}>{applying ? m('creditMemos.applyModal.applying') : m('creditMemos.applyModal.apply')}</button>
		</div>
	</form>
</Modal>

<style>
	/* Page-specific bits not covered by the global design-system CSS in app.css. */
	tr.applied td,
	tr.void td {
		opacity: 0.6;
	}
	/* Explains an empty apply-target list — the memo's vendor has no invoice
	   whose vendor link is resolved and matching, so there is nothing to credit. */
	.modal-hint.warn {
		color: #d4940a;
		margin: -6px 0 0;
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
