<script lang="ts">
	import { api } from '$lib/api';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import ScreeningBadge from '$lib/components/ui/ScreeningBadge.svelte';
	import VendorModal from '$lib/components/modals/VendorModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import type { Vendor, VendorBankDetails } from '$lib/types/vendor';

	const COLUMNS = [
		{ label: 'Vendor' },
		{ label: 'Code' },
		{ label: 'Email' },
		{ label: 'Status' },
		{ label: 'Screening' },
		{ label: 'Source' },
		{ label: 'Invoices' },
		{ label: 'ERP' },
		{ class: 'actions-col' }
	];

	type BankDetails = VendorBankDetails;

	let vendors = $state<Vendor[]>([]);
	let detailVendor = $state<Vendor | null>(null);
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
			const updated = await api.patch<Vendor>(`/api/vendors/${bankEditing.id}`, {
				bank_details: bankForm
			});
			applyVendorUpdate(updated);
			toast('Counterparty saved', 'success');
			bankEditing = null;
		} catch (err) {
			const e = err as { detail?: string; message?: string } | null;
			toast(e?.detail ?? e?.message ?? 'Save failed', 'error');
		} finally {
			savingBank = false;
		}
	}

	// Replace a vendor in the list (and keep the open detail modal in sync) after
	// any mutation — bank edit, screening, risk recompute, block/unblock.
	function applyVendorUpdate(updated: Vendor) {
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

	$effect(() => {
		fetchVendors();
	});

	$effect(() => {
		search;
		statusFilter;
		fetchVendors();
	});

	async function fetchVendors(opts: { append?: boolean; nextPage?: number } = {}) {
		try {
			const nextPage = opts.nextPage ?? 1;
			const params = new URLSearchParams({
				page: String(nextPage),
				page_size: String(PAGE_SIZE)
			});
			if (search.trim()) params.set('search', search.trim());
			if (statusFilter !== 'all') params.set('status', statusFilter);
			const data = await api.get<{ items: Vendor[]; total: number }>(
				`/api/vendors?${params}`
			);
			vendors = opts.append ? [...vendors, ...data.items] : data.items;
			total = data.total;
			page = nextPage;
		} catch {
			toast('Failed to load vendors', 'error');
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

	let unverifiedCount = $derived(vendors.filter(v => v.status === 'unverified').length);

	let statusChips = $derived([
		{ key: 'all', label: 'All', count: vendors.length },
		{
			key: 'unverified',
			label: 'Unverified',
			count: unverifiedCount > 0 ? unverifiedCount : undefined,
			alert: true
		},
		{ key: 'active', label: 'Active' },
		{ key: 'rejected', label: 'Rejected' }
	]);
</script>

<PageHeader title="Vendors">
	{#snippet actions()}
		<button class="btn-outline" disabled={syncing} onclick={syncFromErp}>
			{syncing ? 'Syncing...' : 'Sync from ERP'}
		</button>
	{/snippet}

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder="Search vendors..." ariaLabel="Search vendors" />
		<FilterChips chips={statusChips} bind:active={statusFilter} />
	</div>

	<DataTable columns={COLUMNS} isEmpty={vendors.length === 0} empty="No vendors found.">
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
							<RowAction variant="success" onclick={() => verifyVendor(v.id)}>Verify</RowAction>
							<RowAction variant="danger" onclick={() => rejectVendor(v.id)}>Reject</RowAction>
						{/if}
						<RowAction onclick={() => openBankEditor(v)}>
							{v.bank_details?.counterparty_id ? 'Bank ✓' : 'Bank'}
						</RowAction>
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMoreVendors} disabled={loadingMore}>
				{loadingMore ? 'Loading…' : `Load more (${vendors.length} of ${total})`}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">Showing all {total} vendor{total === 1 ? '' : 's'}</span>
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

<Modal
	open={bankEditing !== null}
	ariaLabel="Vendor bank counterparty"
	onclose={() => (bankEditing = null)}
>
	{#if bankEditing}
		<h2>{bankEditing.name} — bank details</h2>
		<p class="modal-hint">
			These values bridge to your payment processor (e.g. Modern Treasury). The
			<code>counterparty_id</code> is the processor's identifier; the last4s are stored
			here for display only — full account / routing numbers belong with the processor.
		</p>
		<form onsubmit={(e) => { e.preventDefault(); saveBankDetails(); }}>
			<label>
				<span>Processor counterparty ID</span>
				<input type="text" maxlength="255" bind:value={bankForm.counterparty_id} />
			</label>
			<label>
				<span>Bank name</span>
				<input type="text" maxlength="255" bind:value={bankForm.bank_name} />
			</label>
			<div class="form-row">
				<label>
					<span>Account last 4</span>
					<input type="text" maxlength="4" bind:value={bankForm.account_last4} />
				</label>
				<label>
					<span>Routing last 4</span>
					<input type="text" maxlength="4" bind:value={bankForm.routing_last4} />
				</label>
			</div>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (bankEditing = null)}>
					Cancel
				</button>
				<button type="submit" class="btn-primary" disabled={savingBank}>
					{savingBank ? 'Saving…' : 'Save'}
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
