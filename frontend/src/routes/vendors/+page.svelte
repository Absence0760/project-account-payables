<script lang="ts">
	import { api } from '$lib/api';
	import { appendUnique } from '$lib/utils/pagination';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import { untrack } from 'svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import ScreeningBadge from '$lib/components/ui/ScreeningBadge.svelte';
	import VendorModal from '$lib/components/modals/VendorModal.svelte';
	import VendorConsolidationModal from '$lib/components/modals/VendorConsolidationModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { auth } from '$lib/stores/auth.svelte';
	import { PERM_VENDOR_MANAGE } from '$lib/types/admin';
	import { m } from '$lib/i18n/store.svelte';
	import type { Vendor, VendorBankDetails } from '$lib/types/vendor';

	let COLUMNS = $derived([
		{ label: m('vendors.col.vendor') },
		{ label: m('vendors.col.code') },
		{ label: m('vendors.col.email') },
		{ label: m('vendors.col.status') },
		{ label: m('vendors.col.screening') },
		{ label: m('vendors.col.source') },
		{ label: m('vendors.col.invoices') },
		{ label: m('vendors.col.erp') },
		{ class: 'actions-col' }
	]);

	type BankDetails = VendorBankDetails;

	let vendors = $state<Vendor[]>([]);
	let detailVendor = $state<Vendor | null>(null);
	// Vendor consolidation ("Merge into canonical") — admin / ap_manager hold
	// vendor.manage by default; the action surfaces only for them. Gated on the
	// granular permission, not a role check (mirrors the backend gate).
	let showConsolidation = $state(false);
	const canManageVendors = $derived(auth.can(PERM_VENDOR_MANAGE));
	let bankEditing = $state<Vendor | null>(null);
	let bankForm = $state<BankDetails>({
		counterparty_id: '',
		account_last4: '',
		routing_last4: '',
		bank_name: ''
	});
	let savingBank = $state(false);

	function openBankEditor(v: Vendor) {
		bankEditing = v;
		bankForm = {
			counterparty_id: v.bank_details?.counterparty_id ?? '',
			account_last4: v.bank_details?.account_last4 ?? '',
			routing_last4: v.bank_details?.routing_last4 ?? '',
			bank_name: v.bank_details?.bank_name ?? ''
		};
	}

	async function saveBankDetails() {
		if (!bankEditing) return;
		savingBank = true;
		try {
			// Bank-detail changes are dual-control (BEC / bank-redirect gate): this
			// does NOT apply the change — it stages a request a second approver must
			// sign off on via the change-request queue. So we don't optimistically
			// update the row, and the toast says "submitted", not "saved".
			await api.post(`/api/vendors/${bankEditing.id}/bank-change`, {
				bank_details: bankForm
			});
			toast('Bank-detail change submitted for approval', 'success');
			bankEditing = null;
		} catch (err) {
			const e = err as { detail?: string; message?: string } | null;
			toast(e?.detail ?? e?.message ?? 'Submit failed', 'error');
		} finally {
			savingBank = false;
		}
	}

	// Replace a vendor in the list (and keep the open detail modal in sync) after
	// any mutation — bank edit, screening, risk recompute, block/unblock.
	function applyVendorUpdate(updated: Vendor) {
		// A fetch already in flight read this vendor BEFORE the mutation landed,
		// so its response would revert the change (a lifted payment block
		// reappearing, a fresh screening verdict going back to stale). Retire
		// every pre-edit request before applying.
		fetchSequence.supersedeInFlight();
		vendors = vendors.map((v) => (v.id === updated.id ? updated : v));
		if (detailVendor && detailVendor.id === updated.id) detailVendor = updated;
	}
	let search = $state('');
	let statusFilter = $state('all');
	let syncing = $state(false);

	const PAGE_SIZE = 20;
	let total = $state(0);
	let page = $state(1);
	let loadingMore = $state(false);
	let hasMore = $derived(vendors.length < total);

	// Status tallies over the WHOLE (search-scoped) vendor set, from
	// GET /api/vendors/counts — so the chip counts (and the red Unverified
	// attention badge) reflect every page, not just the loaded one.
	let statusCounts = $state<Record<string, number>>({});
	let countsTotal = $state(0);

	const STATUS_LABELS: Record<string, string> = {
		active: 'Active',
		unverified: 'Unverified',
		inactive: 'Inactive',
		rejected: 'Rejected',
	};

	const SOURCE_LABELS: Record<string, string> = {
		manual: 'Manual',
		erp_sync: 'ERP Sync',
		ai_extracted: 'AI Extracted',
	};

	// Debounce timer for search input — mirrors the /invoices and /payments
	// convention (300ms).
	let searchTimer: ReturnType<typeof setTimeout>;
	function debouncedFetch() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => fetchVendors(), 300);
	}

	// Fetch on mount and whenever the status filter chip changes (a chip click
	// is a discrete action, so it fetches immediately — no debounce). This is
	// the ONLY effect that unconditionally calls fetchVendors(); a second
	// effect doing the same on mount used to double-fetch on load.
	$effect(() => {
		statusFilter;
		fetchVendors();
	});

	// Re-fetch on search input (debounced).
	$effect(() => {
		search;
		debouncedFetch();
	});

	// Sequences fetchVendors calls (fetch and load-more alike — one shared
	// counter, latest-issued wins) so a slow response for an earlier
	// search/filter can't land after a faster later one and clobber the list.
	// `applyVendorUpdate` marks in-flight fetches stale the same way, so a
	// response issued before a local edit can't overwrite it either.
	const fetchSequence = createRequestSequencer();

	async function fetchVendors(opts: { append?: boolean; nextPage?: number } = {}) {
		const token = fetchSequence.start();
		try {
			const nextPage = opts.nextPage ?? 1;
			const params = new URLSearchParams({
				page: String(nextPage),
				page_size: String(PAGE_SIZE)
			});
			// `untrack`: this function is also called from the statusFilter
			// `$effect` above. A plain read of `search` here would make THAT
			// effect depend on `search` too (Svelte tracks reads transitively
			// through called functions), so every keystroke would re-fire it —
			// an immediate, un-debounced fetch racing the dedicated debounce
			// timer. `untrack` still reads the current value (the request still
			// carries the live search term); it just stops that read from
			// registering as a dependency of whichever effect calls this.
			const currentSearch = untrack(() => search);
			if (currentSearch.trim()) params.set('search', currentSearch.trim());
			if (statusFilter !== 'all') params.set('status', statusFilter);
			const data = await api.get<{ items: Vendor[]; total: number }>(
				`/api/vendors?${params}`
			);
			// Superseded by a newer fetch, or by a local edit.
			if (!fetchSequence.canCommit(token)) return;
			vendors = opts.append ? appendUnique(vendors, data.items) : data.items;
			total = data.total;
			page = nextPage;
			if (!opts.append) fetchCounts();
		} catch {
			// `isCurrentRequest`, not `canCommit`: a request superseded by a
			// local edit still failed, and no newer request is coming to report
			// it — only a newer *fetch* makes this one's outcome irrelevant.
			if (fetchSequence.isCurrentRequest(token)) toast('Failed to load vendors', 'error');
		}
	}

	async function loadMoreVendors() {
		loadingMore = true;
		try {
			await fetchVendors({ append: true, nextPage: page + 1 });
		} finally {
			loadingMore = false;
		}
	}

	async function fetchCounts() {
		try {
			const params = new URLSearchParams();
			if (search.trim()) params.set('search', search.trim());
			const qs = params.toString();
			const data = await api.get<{ total: number; by_status: Record<string, number> }>(
				`/api/vendors/counts${qs ? `?${qs}` : ''}`
			);
			statusCounts = data.by_status ?? {};
			countsTotal = data.total ?? 0;
		} catch {
			// Non-fatal: chips fall back to the loaded-page tallies below.
			statusCounts = {};
			countsTotal = 0;
		}
	}

	async function verifyVendor(id: string) {
		try {
			await api.post(`/api/vendors/${id}/verify`, {});
			await fetchVendors();
			toast('Vendor verified', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Verify failed', 'error');
		}
	}

	async function rejectVendor(id: string) {
		try {
			await api.post(`/api/vendors/${id}/reject`, {});
			await fetchVendors();
			toast('Vendor rejected', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Reject failed', 'error');
		}
	}

	async function syncFromErp() {
		syncing = true;
		try {
			const result = await api.post<{ message: string }>('/api/vendors/sync-erp', {});
			await fetchVendors();
			toast(result.message, 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Sync failed', 'error');
		} finally {
			syncing = false;
		}
	}

	// Prefer the server-side tallies (whole set); fall back to the loaded page
	// only if the counts endpoint failed (countsTotal === 0 with rows present).
	let allCount = $derived(countsTotal || vendors.length);
	let unverifiedCount = $derived(
		statusCounts.unverified ?? vendors.filter((v) => v.status === 'unverified').length
	);

	let statusChips = $derived([
		{ key: 'all', label: m('common.all'), count: allCount },
		{
			key: 'unverified',
			label: m('vendors.filter.unverified'),
			count: unverifiedCount > 0 ? unverifiedCount : undefined,
			alert: true
		},
		{ key: 'active', label: m('vendors.filter.active') },
		{ key: 'rejected', label: m('vendors.filter.rejected') }
	]);
</script>

<PageHeader title={m('vendors.title')}>
	{#snippet actions()}
		{#if canManageVendors}
			<button class="btn-outline" onclick={() => (showConsolidation = true)}>
				Merge duplicates
			</button>
		{/if}
		<button class="btn-outline" disabled={syncing} onclick={syncFromErp}>
			{syncing ? m('vendors.action.syncing') : m('vendors.action.syncErp')}
		</button>
	{/snippet}

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder={m('vendors.search.placeholder')} ariaLabel={m('vendors.search.aria')} />
		<FilterChips chips={statusChips} bind:active={statusFilter} />
	</div>

	<DataTable columns={COLUMNS} isEmpty={vendors.length === 0} empty={m('vendors.empty')}>
		{#snippet body()}
			{#each vendors as v (v.id)}
				<tr
						class="clickable"
						class:unverified={v.status === 'unverified'}
						class:rejected={v.status === 'rejected'}
						onclick={(e) => {
							if (isRowOpenClick(e)) detailVendor = v;
						}}
					>
						<td class="vendor-name">
							<RowLink onclick={() => (detailVendor = v)} ariaLabel={`Open vendor ${v.name}`}>
								{v.name}
							</RowLink>
						</td>
					<td class="mono muted">{v.code ?? '—'}</td>
					<td class="muted">{v.email ?? '—'}</td>
					<td>
						<span class="status-badge {v.status}">{STATUS_LABELS[v.status] ?? v.status}</span>
					</td>
					<td>
						<ScreeningBadge
							screening={v.screening_status}
							risk={v.risk_level}
							blocked={v.payments_blocked}
						/>
					</td>
					<td>
						<span class="source-badge">{SOURCE_LABELS[v.source] ?? v.source}</span>
					</td>
					<td class="mono">{v.invoice_count}</td>
					<td>
						{#if v.erp_vendor_id}
							<span
								class="erp-linked"
								role="img"
								aria-label={`Linked to ERP: ${v.erp_vendor_id}`}
								title="Linked to ERP: {v.erp_vendor_id}"
							>
								<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
							</span>
						{:else}
							<span class="muted">—</span>
						{/if}
					</td>
					<td class="actions">
						{#if v.status === 'unverified'}
							<RowAction variant="success" onclick={() => verifyVendor(v.id)}>{m('vendors.row.verify')}</RowAction>
							<RowAction variant="danger" onclick={() => rejectVendor(v.id)}>{m('vendors.row.reject')}</RowAction>
						{/if}
						<RowAction onclick={() => openBankEditor(v)}>
							{v.bank_details?.counterparty_id ? m('vendors.row.bankSet') : m('vendors.row.bank')}
						</RowAction>
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMoreVendors} disabled={loadingMore}>
				{loadingMore ? m('common.loading') : m('vendors.loadMore', { shown: vendors.length, total })}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('vendors.showingAll', { total })}</span>
		</div>
	{/if}
</PageHeader>

{#if detailVendor}
	<VendorModal
		vendor={detailVendor}
		onclose={() => (detailVendor = null)}
		onupdated={applyVendorUpdate}
	/>
{/if}

{#if showConsolidation}
	<VendorConsolidationModal
		onclose={() => (showConsolidation = false)}
		onmerged={() => fetchVendors()}
	/>
{/if}

<Modal
	open={bankEditing !== null}
	ariaLabel="Vendor bank counterparty"
	onclose={() => (bankEditing = null)}
>
	{#if bankEditing}
		<h2>{m('vendors.bank.title', { vendor: bankEditing.name })}</h2>
		<p class="modal-hint">
			These values bridge to your payment processor (e.g. Modern Treasury). The
			<code>counterparty_id</code> is the processor's identifier; the last4s are stored
			here for display only — full account / routing numbers belong with the processor.
		</p>
		<form onsubmit={(e) => { e.preventDefault(); saveBankDetails(); }}>
			<label>
				<span>{m('vendors.bank.counterpartyId')}</span>
				<input type="text" maxlength="255" bind:value={bankForm.counterparty_id} />
			</label>
			<label>
				<span>{m('vendors.bank.bankName')}</span>
				<input type="text" maxlength="255" bind:value={bankForm.bank_name} />
			</label>
			<div class="form-row">
				<label>
					<span>{m('vendors.bank.accountLast4')}</span>
					<input type="text" maxlength="4" bind:value={bankForm.account_last4} />
				</label>
				<label>
					<span>{m('vendors.bank.routingLast4')}</span>
					<input type="text" maxlength="4" bind:value={bankForm.routing_last4} />
				</label>
			</div>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (bankEditing = null)}>
					{m('common.cancel')}
				</button>
				<button type="submit" class="btn-primary" disabled={savingBank}>
					{savingBank ? m('common.saving') : m('common.save')}
				</button>
			</div>
		</form>
	{/if}
</Modal>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */
	.btn-outline {
		padding: 8px 18px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
	}
	.btn-outline:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.btn-outline:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	/* Row emphasis + bespoke cell badges */
	tr:last-child td {
		border-bottom: none;
	}
	.unverified {
		background: rgba(212, 148, 10, 0.04);
	}
	/* De-emphasize rejected rows with a subtle tint rather than a blanket
	   opacity (which would composite every cell's text below the WCAG
	   1.4.3 4.5:1 contrast floor). The red "Rejected" status badge carries
	   the state signal. */
	.rejected td {
		background: rgba(224, 64, 64, 0.04);
	}
	.vendor-name {
		font-weight: 500;
	}
	.status-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.75rem;
		font-weight: 500;
	}
	.status-badge.active {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}
	.status-badge.unverified {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}
	.status-badge.inactive {
		background: var(--bg);
		color: var(--text-muted);
	}
	.status-badge.rejected {
		background: rgba(224, 64, 64, 0.12);
		color: #f06464;
	}
	.source-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.72rem;
		font-weight: 500;
		background: var(--bg);
		color: var(--text-muted);
	}
	.erp-linked {
		color: #1fa86a;
		display: inline-flex;
	}

	/* Bank-counterparty modal extras */
	.modal-hint code {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.78rem;
		color: var(--text);
	}
	.modal input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}
	.form-row {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 12px;
	}
</style>
