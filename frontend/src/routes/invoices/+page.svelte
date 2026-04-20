<script lang="ts">
	import type { Invoice, InvoiceStatus, AdvancedSearchFilters } from '$lib/types/invoice';
	import { INVOICE_STATUSES, STATUS_LABELS, EMPTY_ADVANCED_FILTERS, SYSTEM_MANAGED_STATUSES, commonTransitions } from '$lib/types/invoice';
	import { invoiceStore } from '$lib/stores/invoices.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { api } from '$lib/api';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import InvoiceModal from '$lib/components/InvoiceModal.svelte';
	import AdvancedSearchModal from '$lib/components/AdvancedSearchModal.svelte';
	import { toast } from '$lib/components/Toast.svelte';
	import { workflowStore } from '$lib/stores/workflows.svelte';

	let search = $state('');
	let activeStatuses = $state<InvoiceStatus[]>([]);
	let editing = $state<Invoice | null>(null);
	let showAdvancedSearch = $state(false);
	let advancedFilters = $state<AdvancedSearchFilters>({ ...EMPTY_ADVANCED_FILTERS });
	let uploading = $state(false);
	let fileInput: HTMLInputElement;

	async function handleUpload(e: Event) {
		const input = e.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		uploading = true;
		try {
			await api.upload('/api/invoices/upload', file);
			await invoiceStore.fetch(buildParams());
			await invoiceStore.fetchCounts();
			toast('Invoice uploaded successfully', 'success');
		} catch (err: unknown) {
			toast(err instanceof Error ? err.message : 'Upload failed', 'error');
		} finally {
			uploading = false;
			input.value = '';
		}
	}

	// Build query params from current filters and fetch from API
	function toggleStatus(s: InvoiceStatus) {
		if (activeStatuses.includes(s)) {
			activeStatuses = activeStatuses.filter((x) => x !== s);
		} else {
			activeStatuses = [...activeStatuses, s];
		}
	}

	function buildParams(): Record<string, string> {
		const params: Record<string, string> = { page_size: '100' };
		if (activeStatuses.length > 0) params.status = activeStatuses.join(',');
		if (search.trim()) params.search = search.trim();
		const af = advancedFilters;
		if (af.vendor) params.vendor = af.vendor;
		if (af.invoice_number) params.invoice_number = af.invoice_number;
		if (af.po_number) params.po_number = af.po_number;
		if (af.description) params.description = af.description;
		if (af.amount_min) params.amount_min = af.amount_min;
		if (af.amount_max) params.amount_max = af.amount_max;
		if (af.due_date_from) params.due_date_from = af.due_date_from;
		if (af.due_date_to) params.due_date_to = af.due_date_to;
		if (af.statuses.length > 0) params.status = af.statuses.join(',');
		return params;
	}

	// Debounce timer for search input
	let searchTimer: ReturnType<typeof setTimeout>;
	function debouncedFetch() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => invoiceStore.fetch(buildParams()), 300);
	}

	// Fetch status counts and active workflow on mount
	$effect(() => {
		invoiceStore.fetchCounts();
		workflowStore.fetchActiveSteps();
	});

	// Re-fetch when status filters or advanced filters change
	$effect(() => {
		activeStatuses;
		advancedFilters;
		invoiceStore.fetch(buildParams());
	});

	// Re-fetch on search input (debounced)
	$effect(() => {
		search;
		debouncedFetch();
	});

	let hasAdvancedFilters = $derived(
		advancedFilters.vendor !== '' ||
		advancedFilters.invoice_number !== '' ||
		advancedFilters.po_number !== '' ||
		advancedFilters.description !== '' ||
		advancedFilters.amount_min !== '' ||
		advancedFilters.amount_max !== '' ||
		advancedFilters.due_date_from !== '' ||
		advancedFilters.due_date_to !== '' ||
		advancedFilters.statuses.length > 0
	);

	let totalCount = $derived(
		Object.values(invoiceStore.statusCounts).reduce((a, b) => a + b, 0)
	);

	// Only show statuses relevant to the active workflow
	let visibleStatuses = $derived.by(() => {
		const s = workflowStore.activeSteps;
		const visible: InvoiceStatus[] = ['new'];
		if (s.extraction) visible.push('pending');
		if (s.approval) visible.push('ready_for_review', 'approved', 'rejected');
		if (s.erp_export) visible.push('sending_to_erp', 'sent_to_erp');
		visible.push('done', 'failed');
		return visible;
	});

	function statusCount(status: InvoiceStatus): number {
		return invoiceStore.statusCounts[status] ?? 0;
	}

	// --- Selection & bulk ops ---
	let selected = $state<Set<string>>(new Set());
	let bulkBusy = $state(false);
	let bulkStatusValue = $state<InvoiceStatus>('approved');
	let showBulkStatusSelect = $state(false);

	let selectableInvoices = $derived(
		invoiceStore.all.filter((inv) => !SYSTEM_MANAGED_STATUSES.has(inv.status))
	);

	let allSelected = $derived(
		selectableInvoices.length > 0 && selectableInvoices.every((inv) => selected.has(inv.id))
	);

	let selectedStatuses = $derived.by(() => {
		const statuses = new Set<InvoiceStatus>();
		for (const inv of invoiceStore.all) {
			if (selected.has(inv.id)) statuses.add(inv.status);
		}
		return [...statuses];
	});

	let validBulkTransitions = $derived(commonTransitions(selectedStatuses));

	// Reset bulkStatusValue when the valid options change
	$effect(() => {
		if (validBulkTransitions.length > 0 && !validBulkTransitions.includes(bulkStatusValue)) {
			bulkStatusValue = validBulkTransitions[0];
		}
	});

	// Whether any selected invoice is in an immutable status (for delete)
	let hasImmutableSelected = $derived(
		selectedStatuses.some((s) => SYSTEM_MANAGED_STATUSES.has(s))
	);

	function toggleSelect(id: string) {
		const next = new Set(selected);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		selected = next;
	}

	function toggleSelectAll() {
		if (allSelected) {
			selected = new Set();
		} else {
			selected = new Set(selectableInvoices.map((inv) => inv.id));
		}
	}

	async function bulkDelete() {
		bulkBusy = true;
		try {
			const res = await api.post('/api/invoices/bulk/delete', { ids: [...selected] }) as { deleted: number; skipped: string[] };
			await invoiceStore.fetch(buildParams());
			await invoiceStore.fetchCounts();
			selected = new Set();
			const msg = res.skipped?.length
				? `Deleted ${res.deleted}, skipped ${res.skipped.length} (immutable)`
				: `Deleted ${res.deleted} invoice(s)`;
			toast(msg, 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Bulk delete failed', 'error');
		} finally {
			bulkBusy = false;
		}
	}

	async function bulkStatusChange() {
		bulkBusy = true;
		try {
			const res = await api.post('/api/invoices/bulk/status', {
				ids: [...selected],
				status: bulkStatusValue,
			}) as { updated: number; skipped: string[] };
			await invoiceStore.fetch(buildParams());
			await invoiceStore.fetchCounts();
			selected = new Set();
			showBulkStatusSelect = false;
			const msg = res.skipped?.length
				? `Updated ${res.updated}, skipped ${res.skipped.length} (immutable)`
				: `Updated ${res.updated} invoice(s)`;
			toast(msg, 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Bulk status change failed', 'error');
		} finally {
			bulkBusy = false;
		}
	}

	async function bulkExport(format: string) {
		bulkBusy = true;
		try {
			const { PUBLIC_API_URL } = await import('$env/static/public');
			const base = PUBLIC_API_URL.replace(/\/+$/, '');
			const token = localStorage.getItem('auth_token');
			const res = await fetch(`${base}/api/invoices/bulk/export`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					...(token ? { Authorization: `Bearer ${token}` } : {}),
					'X-Tenant-Slug': document.location.hostname.split('.')[0],
				},
				body: JSON.stringify({ ids: [...selected], format }),
			});
			if (!res.ok) throw new Error(`Export failed: ${res.status}`);
			if (format === 'json') {
				const data = await res.json();
				const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
				triggerDownload(blob, `invoices-export.json`);
			} else {
				const blob = await res.blob();
				triggerDownload(blob, `invoices-export.${format}`);
			}
			toast(`Exported ${selected.size} invoice(s)`, 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Bulk export failed', 'error');
		} finally {
			bulkBusy = false;
		}
	}

	function triggerDownload(blob: Blob, filename: string) {
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = filename;
		a.click();
		URL.revokeObjectURL(url);
	}

	let deletingId = $state<string | null>(null);
	let confirmDeleteId = $state<string | null>(null);
	let confirmBulkDelete = $state(false);

	const IMMUTABLE_STATUSES = new Set(['done', 'sent_to_erp', 'sending_to_erp']);

	async function deleteInvoice(id: string) {
		deletingId = id;
		try {
			await api.delete(`/api/invoices/${id}`);
			await invoiceStore.fetch(buildParams());
			await invoiceStore.fetchCounts();
			toast('Invoice deleted', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Delete failed', 'error');
		} finally {
			deletingId = null;
			confirmDeleteId = null;
		}
	}

	function handleWindowClick(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (confirmDeleteId && !target.closest('.delete-btn')) {
			confirmDeleteId = null;
		}
		if (confirmBulkDelete && !target.closest('.bulk-delete-btn')) {
			confirmBulkDelete = false;
		}
		if (showBulkStatusSelect && !target.closest('.bulk-status-wrapper')) {
			showBulkStatusSelect = false;
		}
	}

	function formatCurrency(amount: number, currency: string): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount);
	}
</script>

<svelte:window onclick={handleWindowClick} />

<div class="workspace">
	<header class="toolbar">
		<div class="search-group">
			<div class="search-box">
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
				</svg>
				<input type="text" placeholder="Search invoices..." bind:value={search} />
			</div>
			<button
				class="advanced-btn"
				class:has-filters={hasAdvancedFilters}
				onclick={() => (showAdvancedSearch = true)}
				aria-label="Advanced search"
			>
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<line x1="4" y1="6" x2="20" y2="6" />
					<line x1="8" y1="12" x2="20" y2="12" />
					<line x1="12" y1="18" x2="20" y2="18" />
					<circle cx="6" cy="12" r="2" /><circle cx="10" cy="18" r="2" /><circle cx="8" cy="6" r="2" />
				</svg>
				{#if hasAdvancedFilters}
					<span class="dot"></span>
				{/if}
			</button>
		</div>
		<input type="file" accept=".pdf,.png,.jpg,.jpeg,.tiff" bind:this={fileInput} onchange={handleUpload} hidden />
		<button class="btn-upload" disabled={uploading} onclick={() => fileInput.click()}>
			{uploading ? 'Uploading...' : '+ Upload Invoice'}
		</button>
	</header>

	<nav class="filters">
		<button class="filter-chip" class:active={activeStatuses.length === 0} onclick={() => (activeStatuses = [])}>
			All <span class="count">{totalCount}</span>
		</button>
		{#each visibleStatuses as s}
			<button class="filter-chip" class:active={activeStatuses.includes(s)} onclick={() => toggleStatus(s)}>
				{STATUS_LABELS[s]} <span class="count">{statusCount(s)}</span>
			</button>
		{/each}
	</nav>

	{#if selected.size > 0}
		<div class="bulk-bar">
			<span class="bulk-count">{selected.size} selected</span>
			<button class="bulk-clear" onclick={() => (selected = new Set())}>Clear</button>
			<div class="bulk-divider"></div>

			{#if !auth.isClerkOnly}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<span class="bulk-btn-wrap" title={hasImmutableSelected ? 'Cannot delete invoices in system-managed statuses' : ''}>
					<button
						class="bulk-delete-btn"
						class:armed={confirmBulkDelete}
						disabled={bulkBusy || hasImmutableSelected}
						onclick={(e) => {
							e.stopPropagation();
							if (confirmBulkDelete) {
								bulkDelete();
							} else {
								confirmBulkDelete = true;
							}
						}}
					>
						{#if confirmBulkDelete}
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
							Confirm Delete
						{:else}
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
							Delete
						{/if}
					</button>
				</span>

				<div class="bulk-status-wrapper">
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<span class="bulk-btn-wrap" title={validBulkTransitions.length === 0 ? 'No common status transitions for the selected invoices' : ''}>
						<button
							class="bulk-action-btn"
							disabled={bulkBusy || validBulkTransitions.length === 0}
							onclick={(e) => { e.stopPropagation(); showBulkStatusSelect = !showBulkStatusSelect; }}
						>
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
							Change Status
						</button>
					</span>
					{#if showBulkStatusSelect && validBulkTransitions.length > 0}
						<div class="bulk-status-dropdown">
							<select bind:value={bulkStatusValue}>
								{#each validBulkTransitions as s}
									<option value={s}>{STATUS_LABELS[s]}</option>
								{/each}
							</select>
							<button class="bulk-apply-btn" disabled={bulkBusy} onclick={bulkStatusChange}>Apply</button>
						</div>
					{/if}
				</div>
			{/if}

			<div class="bulk-divider"></div>

			<button class="bulk-action-btn" disabled={bulkBusy} onclick={() => bulkExport('csv')}>CSV</button>
			<button class="bulk-action-btn" disabled={bulkBusy} onclick={() => bulkExport('json')}>JSON</button>
			<button class="bulk-action-btn" disabled={bulkBusy} onclick={() => bulkExport('xml')}>XML</button>
		</div>
	{/if}

	<div class="grid-container">
		<table>
			<thead>
				<tr>
					<th class="checkbox-col"><input type="checkbox" checked={allSelected} onchange={toggleSelectAll} /></th>
					<th class="actions-col"></th>
					<th>Invoice #</th>
					<th>Vendor</th>
					<th>Description</th>
					<th>PO #</th>
					<th class="right">Amount</th>
					<th>Due Date</th>
					<th>Status</th>
				</tr>
			</thead>
			<tbody>
				{#each invoiceStore.all as invoice (invoice.id)}
					<tr class:row-selected={selected.has(invoice.id)}>
						<td class="checkbox-col" title={SYSTEM_MANAGED_STATUSES.has(invoice.status) ? `Cannot select — ${STATUS_LABELS[invoice.status]} is system-managed` : ''}><input type="checkbox" checked={selected.has(invoice.id)} disabled={SYSTEM_MANAGED_STATUSES.has(invoice.status)} onchange={() => toggleSelect(invoice.id)} /></td>
						<td class="actions">
							<button class="edit-btn" onclick={() => (editing = invoice)}>Edit</button>
							{#if !auth.isClerkOnly && !IMMUTABLE_STATUSES.has(invoice.status)}
								<button
									class="delete-btn"
									class:armed={confirmDeleteId === invoice.id}
									disabled={deletingId === invoice.id}
									onclick={(e) => {
										e.stopPropagation();
										if (confirmDeleteId === invoice.id) {
											deleteInvoice(invoice.id);
										} else {
											confirmDeleteId = invoice.id;
										}
									}}
								>
									{#if deletingId === invoice.id}
										<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" stroke-dasharray="31.4" stroke-dashoffset="10"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>
									{:else if confirmDeleteId === invoice.id}
										<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
									{:else}
										<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
									{/if}
								</button>
							{/if}
						</td>
						<td class="mono">
							{invoice.invoice_number || '—'}
							{#if invoice.warnings?.length}
								<span class="warning-icon" title={invoice.warnings.map(w => w.message).join(', ')}>
									<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
										<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
									</svg>
								</span>
							{/if}
						</td>
						<td>
							{invoice.vendor || '—'}
							{#if invoice.priors_summary && (invoice.priors_summary.cache > 0 || invoice.priors_summary.rag > 0)}
								<span
									class="priors-badge"
									title="Extraction priors: {invoice.priors_summary.cache} vendor-cache field{invoice.priors_summary.cache === 1 ? '' : 's'}, {invoice.priors_summary.rag} RAG neighbor{invoice.priors_summary.rag === 1 ? '' : 's'}"
								>
									{#if invoice.priors_summary.rag > 0}RAG·{invoice.priors_summary.rag}{/if}{#if invoice.priors_summary.cache > 0 && invoice.priors_summary.rag > 0}·{/if}{#if invoice.priors_summary.cache > 0}cache·{invoice.priors_summary.cache}{/if}
								</span>
							{/if}
						</td>
						<td class="description" title={invoice.description}>{invoice.description}</td>
						<td class="mono">{invoice.po_number}</td>
						<td class="right mono">{formatCurrency(invoice.amount, invoice.currency)}</td>
						<td>{invoice.due_date}</td>
						<td><StatusBadge status={invoice.status} /></td>
					</tr>
				{:else}
					<tr>
						<td colspan="9" class="empty">No invoices match your filters.</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>

{#if editing}
	<InvoiceModal invoice={editing} onclose={() => (editing = null)} activeSteps={workflowStore.activeSteps} />
{/if}

{#if showAdvancedSearch}
	<AdvancedSearchModal
		filters={advancedFilters}
		onclose={() => (showAdvancedSearch = false)}
		onapply={(f) => (advancedFilters = f)}
	/>
{/if}

<style>
	.workspace {
		max-width: 1280px;
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
		gap: 16px;
	}

	.search-group {
		display: flex;
		align-items: center;
		gap: 6px;
		flex: 1;
	}

	.search-box {
		display: flex;
		align-items: center;
		gap: 8px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 20px;
		padding: 8px 14px;
		flex: 1;
		max-width: 480px;
		color: var(--text-muted);
	}

	.advanced-btn {
		position: relative;
		display: grid;
		place-items: center;
		width: 34px;
		height: 34px;
		border-radius: 50%;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		cursor: pointer;
		transition: all 0.15s;
		flex-shrink: 0;
	}

	.advanced-btn:hover {
		border-color: var(--accent);
		color: var(--text);
	}

	.advanced-btn.has-filters {
		border-color: var(--accent);
		color: var(--accent);
	}

	.advanced-btn .dot {
		position: absolute;
		top: 4px;
		right: 4px;
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--accent);
	}

	.search-box input {
		border: none;
		background: none;
		outline: none;
		font-size: 0.9rem;
		width: 100%;
		color: var(--text);
		font-family: inherit;
	}

	.search-box input::placeholder {
		color: var(--text-muted);
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

	.filter-chip .count {
		font-size: 0.72rem;
		opacity: 0.7;
	}

	.grid-container {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow-x: auto;
		/* Workspace is `display: flex; flex-direction: column`, so children
		   inherit `min-width: auto` = intrinsic content width. Without
		   min-width: 0, this card grows to fit the table and the page
		   scrolls horizontally instead of the card scrolling internally. */
		min-width: 0;
		max-width: 100%;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.875rem;
	}

	thead {
		position: sticky;
		top: 0;
		z-index: 1;
	}

	th {
		background: var(--bg);
		text-align: left;
		padding: 10px 14px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
		white-space: nowrap;
	}

	td {
		padding: 10px 14px;
		border-bottom: 1px solid var(--border);
		color: var(--text);
		white-space: nowrap;
		vertical-align: middle;
	}

	.description {
		max-width: 220px;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	tr:last-child td {
		border-bottom: none;
	}

	tbody tr:hover {
		background: rgba(99, 140, 255, 0.04);
	}

	.mono {
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
		font-size: 0.82rem;
	}

	.right {
		text-align: right;
	}

	.edit-btn {
		padding: 4px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.edit-btn:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.actions {
		display: flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
	}

	.delete-btn {
		display: grid;
		place-items: center;
		width: 30px;
		height: 28px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		cursor: pointer;
		transition: all 0.15s;
	}

	.delete-btn:hover {
		border-color: #e04040;
		color: #e04040;
	}

	.delete-btn.armed {
		border-color: #e04040;
		background: rgba(224, 64, 64, 0.1);
		color: #e04040;
	}

	.delete-btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.empty {
		text-align: center;
		padding: 40px 14px;
		color: var(--text-muted);
	}

	.warning-icon {
		display: inline-flex;
		vertical-align: middle;
		margin-left: 4px;
		color: #d4940a;
		cursor: help;
	}

	.priors-badge {
		display: inline-block;
		margin-left: 6px;
		padding: 1px 6px;
		border-radius: 4px;
		background: rgba(99, 140, 255, 0.15);
		color: var(--accent);
		font-size: 0.68rem;
		font-weight: 600;
		letter-spacing: 0.02em;
		vertical-align: middle;
		cursor: help;
	}

	.btn-upload {
		padding: 8px 18px;
		border-radius: 6px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		transition: opacity 0.15s;
		white-space: nowrap;
		flex-shrink: 0;
	}

	.btn-upload:hover {
		opacity: 0.85;
	}

	.btn-upload:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.upload-error {
		padding: 10px 14px;
		border-radius: 6px;
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
		font-size: 0.85rem;
	}

	/* --- Checkbox column --- */

	.checkbox-col {
		width: 36px;
		text-align: center;
		padding-left: 10px;
		padding-right: 4px;
	}

	.checkbox-col input[type='checkbox'] {
		cursor: pointer;
		accent-color: var(--accent);
	}

	.row-selected {
		background: rgba(99, 140, 255, 0.08);
	}

	/* --- Bulk action bar --- */

	/* Floats above the page so selecting rows doesn't shove the table down.
	   Bottom-centered so it lands in the natural eye line for an "I'm
	   acting on the selection" affordance. */
	.bulk-bar {
		position: fixed;
		left: 50%;
		bottom: 24px;
		transform: translateX(-50%);
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 16px;
		background: var(--surface);
		border: 1px solid var(--accent);
		border-radius: 8px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
		flex-wrap: wrap;
		z-index: 50;
		max-width: calc(100vw - 48px);
	}

	.bulk-count {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--accent);
		white-space: nowrap;
	}

	.bulk-clear {
		padding: 4px 10px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.bulk-clear:hover {
		color: var(--text);
		background: var(--bg);
	}

	.bulk-btn-wrap {
		display: inline-flex;
	}

	.bulk-divider {
		width: 1px;
		height: 20px;
		background: var(--border);
	}

	.bulk-action-btn {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 5px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
	}

	.bulk-action-btn:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.bulk-action-btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.bulk-delete-btn {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 5px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
		transition: all 0.15s;
	}

	.bulk-delete-btn:hover {
		border-color: #e04040;
		color: #e04040;
	}

	.bulk-delete-btn.armed {
		border-color: #e04040;
		background: rgba(224, 64, 64, 0.1);
		color: #e04040;
	}

	.bulk-delete-btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	/* --- Bulk status dropdown --- */

	.bulk-status-wrapper {
		position: relative;
	}

	.bulk-status-dropdown {
		position: absolute;
		bottom: calc(100% + 6px);
		left: 0;
		display: flex;
		align-items: center;
		gap: 6px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 8px 10px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
		z-index: 20;
		white-space: nowrap;
	}

	.bulk-status-dropdown select {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 5px 8px;
		font-size: 0.82rem;
		color: var(--text);
		font-family: inherit;
	}

	.bulk-apply-btn {
		padding: 5px 12px;
		border-radius: 4px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.8rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.bulk-apply-btn:hover:not(:disabled) {
		opacity: 0.9;
	}

	.bulk-apply-btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
</style>
