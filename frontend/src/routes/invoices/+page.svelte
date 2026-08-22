<script lang="ts">
	import type { Invoice, InvoiceStatus, AdvancedSearchFilters } from '$lib/types/invoice';
	import { INVOICE_STATUSES, STATUS_LABELS, EMPTY_ADVANCED_FILTERS, SYSTEM_MANAGED_STATUSES, IMMUTABLE_STATUSES, commonTransitions } from '$lib/types/invoice';
	import { invoiceStore } from '$lib/stores/invoices.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { api } from '$lib/api';
	import StatusBadge from '$lib/components/ui/StatusBadge.svelte';
	import InvoiceModal from '$lib/components/modals/InvoiceModal.svelte';
	import CreateInvoiceModal from '$lib/components/modals/CreateInvoiceModal.svelte';
	import AdvancedSearchModal from '$lib/components/modals/AdvancedSearchModal.svelte';
	import BulkRecodeGLModal from '$lib/components/modals/BulkRecodeGLModal.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { pruneSelection } from '$lib/utils/selection';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { workflowStore } from '$lib/stores/workflows.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { untrack } from 'svelte';

	let search = $state('');
	let activeStatuses = $state<InvoiceStatus[]>([]);
	let editing = $state<Invoice | null>(null);
	let showAdvancedSearch = $state(false);
	let advancedFilters = $state<AdvancedSearchFilters>({ ...EMPTY_ADVANCED_FILTERS });
	let uploading = $state(false);
	let uploadProgress = $state('');
	let fileInput: HTMLInputElement;
	let showBulkRecode = $state(false);
	let showCreate = $state(false);

	async function handleInvoiceCreated() {
		await invoiceStore.fetch(buildParams());
		await invoiceStore.fetchCounts();
	}

	async function handleUpload(e: Event) {
		const input = e.target as HTMLInputElement;
		const files = input.files;
		if (!files || files.length === 0) return;
		uploading = true;

		const total = files.length;
		let succeeded = 0;
		let failed = 0;
		const BATCH = 5;

		for (let i = 0; i < total; i += BATCH) {
			const batch = Array.from(files).slice(i, i + BATCH);
			uploadProgress = total > 1 ? `Uploading ${Math.min(i + BATCH, total)} of ${total}...` : 'Uploading...';
			const results = await Promise.allSettled(
				batch.map((file) => api.upload('/api/invoices/upload', file))
			);
			for (let j = 0; j < results.length; j++) {
				if (results[j].status === 'fulfilled') {
					succeeded++;
				} else {
					failed++;
					const reason = (results[j] as PromiseRejectedResult).reason;
					toast(`Failed to upload ${batch[j].name}: ${reason instanceof Error ? reason.message : 'Unknown error'}`, 'error');
				}
			}
		}

		// The refetch is the last thing that can throw, and it happens AFTER the
		// files are already uploaded. Without the `finally` a failed refetch left
		// `uploading` stuck true — the button read "Uploading…" forever, disabled,
		// and re-picking the same file wouldn't even fire `change` (the input's
		// value was never cleared). Only a page reload recovered.
		try {
			await invoiceStore.fetch(buildParams());
			await invoiceStore.fetchCounts();

			if (total === 1 && succeeded === 1) {
				toast('Invoice uploaded successfully', 'success');
			} else if (total > 1) {
				toast(`Uploaded ${succeeded} of ${total} invoice${total > 1 ? 's' : ''}${failed ? ` (${failed} failed)` : ''}`, succeeded > 0 ? 'success' : 'error');
			}
		} catch (err) {
			// The upload itself succeeded — say so, and say the list is stale.
			toast(
				err instanceof Error
					? `Uploaded, but the list could not be refreshed: ${err.message}`
					: 'Uploaded, but the list could not be refreshed',
				'error'
			);
		} finally {
			uploading = false;
			uploadProgress = '';
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
		const params: Record<string, string> = {};
		if (activeStatuses.length > 0) params.status = activeStatuses.join(',');
		// `untrack`: `buildParams()` is also called from the status/advanced-filter
		// `$effect` below. A plain read of `search` here would make THAT effect
		// depend on `search` too (Svelte tracks reads transitively through called
		// functions), so every keystroke would re-fire it — an immediate,
		// un-debounced fetch racing the dedicated debounce timer further down.
		// `untrack` still reads the current value (the request still carries the
		// live search term); it just stops that read from registering as a
		// dependency of whichever effect happens to be calling this.
		const currentSearch = untrack(() => search);
		if (currentSearch.trim()) params.search = currentSearch.trim();
		const af = advancedFilters;
		if (af.vendor) params.vendor = af.vendor;
		if (af.invoice_number) params.invoice_number = af.invoice_number;
		if (af.po_number) params.po_number = af.po_number;
		if (af.description) params.description = af.description;
		if (af.amount_min) params.amount_min = af.amount_min;
		if (af.amount_max) params.amount_max = af.amount_max;
		if (af.due_date_from) params.due_date_from = af.due_date_from;
		if (af.due_date_to) params.due_date_to = af.due_date_to;
		// Status is sourced solely from `activeStatuses` (the inline chips); the
		// modal's Status section writes back into `activeStatuses` on apply, so
		// the two never fight over `params.status`.
		return params;
	}

	// Debounce timer for search input
	let searchTimer: ReturnType<typeof setTimeout>;
	function debouncedFetch() {
		clearTimeout(searchTimer);
		// `.catch` because nothing awaits this: the store re-throws so an
		// awaiting caller keeps its own handling (the post-upload toast relies
		// on it), and `invoiceStore.errored` is what the table renders.
		searchTimer = setTimeout(() => invoiceStore.fetch(buildParams()).catch(() => {}), 300);
	}

	// Fetch status counts and active workflow on mount
	$effect(() => {
		// Fire-and-forget: see the note in debouncedFetch — the stores' own
		// `errored` flags are what the UI reads.
		invoiceStore.fetchCounts().catch(() => {});
		workflowStore.fetchActiveSteps().catch(() => {});
	});

	// Re-fetch when status filters or advanced filters change
	$effect(() => {
		activeStatuses;
		advancedFilters;
		invoiceStore.fetch(buildParams()).catch(() => {});
	});

	// Re-fetch on search input (debounced)
	$effect(() => {
		search;
		debouncedFetch();
		// Cancel a pending debounce on teardown: without it the timer fires
		// after the page is gone and lands a stale list into the shared store.
		return () => clearTimeout(searchTimer);
	});

	// Deep-link: `/invoices?id=<uuid>` (e.g. the "Invoice" action on the
	// exceptions queue) opens that invoice's detail modal on load. The row
	// may live on a later page than the default 20 we fetch, so resolve it
	// straight from the API rather than the in-memory list.
	let deepLinkLoaded = $state<string | null>(null);
	$effect(() => {
		const id = $page.url.searchParams.get('id');
		if (!id || deepLinkLoaded === id) return;
		deepLinkLoaded = id;
		api
			.get<Invoice>(`/api/invoices/${id}`)
			.then((inv) => (editing = inv))
			.catch(() => toast('Invoice not found', 'error'));
	});

	/**
	 * Refresh the list + chip counts with THIS page's active filters.
	 *
	 * Handed to `InvoiceModal` as `onrefresh` so a refresh triggered from
	 * inside the modal (an extraction poll finishing, a contract link) goes
	 * through the filters rather than the store's param-less `fetch()`, which
	 * would widen the list to every status and reset `lastParams` so Load-more
	 * paged a different set.
	 */
	async function refreshInvoiceList() {
		await invoiceStore.fetch(buildParams());
		await invoiceStore.fetchCounts();
	}

	function closeInvoiceModal() {
		editing = null;
		const url = new URL($page.url);
		if (url.searchParams.has('id')) {
			url.searchParams.delete('id');
			replaceState(`${url.pathname}${url.search}`, {});
			// Allow re-opening the same invoice from a fresh deep-link click.
			deepLinkLoaded = null;
		}
		// Re-apply this page's active filters. The modal's mutation handlers
		// (approve / reject / save / …) deliberately don't refresh the list
		// themselves — an unfiltered refetch would leave every status showing,
		// so a just-approved invoice reappears under an active "Ready for
		// Review" chip. Refetching with buildParams() on close restores the
		// filtered view and keeps the chip counts honest. Fire-and-forget: the
		// modal is already gone, and `invoiceStore.errored` renders a failure.
		void refreshInvoiceList().catch(() => {});
	}

	let hasAdvancedFilters = $derived(
		advancedFilters.vendor !== '' ||
		advancedFilters.invoice_number !== '' ||
		advancedFilters.po_number !== '' ||
		advancedFilters.description !== '' ||
		advancedFilters.amount_min !== '' ||
		advancedFilters.amount_max !== '' ||
		advancedFilters.due_date_from !== '' ||
		advancedFilters.due_date_to !== ''
	);

	let totalCount = $derived(
		Object.values(invoiceStore.statusCounts).reduce((a, b) => a + b, 0)
	);

	// Quick-access status chips: the actionable stages people triage daily.
	// The full status set lives in the Advanced Search modal — these are the
	// high-traffic subset surfaced inline (with live counts). Gated on the
	// active workflow so approval-only states don't appear without an approval
	// step. The transient/terminal stages (Extracting, Sending/Sent to ERP,
	// Rejected, Done, and the post-ERP payment states) stay in the modal.
	let quickStatuses = $derived.by(() => {
		const s = workflowStore.activeSteps;
		const quick: InvoiceStatus[] = ['new'];
		if (s.approval) quick.push('ready_for_review', 'approved');
		quick.push('failed');
		return quick;
	});

	// Chips actually rendered = the quick subset plus any active status that
	// isn't in the subset (e.g. one picked in the Advanced Search modal), so an
	// active status filter is never invisible. Canonical workflow order.
	let chipStatuses = $derived.by(() => {
		const quick = new Set(quickStatuses);
		return INVOICE_STATUSES.filter((s) => quick.has(s) || activeStatuses.includes(s));
	});

	// Three states, not two: a failed load must not read as "nothing matched".
	let emptyMessage = $derived(
		invoiceStore.errored ? m('invoices.empty.errored') : m('invoices.empty')
	);

	function statusCount(status: InvoiceStatus): number {
		return invoiceStore.statusCounts[status] ?? 0;
	}

	// --- Selection & bulk ops ---
	let selected = $state<Set<string>>(new Set());
	let bulkBusy = $state(false);
	let bulkStatusValue = $state<InvoiceStatus>('approved');
	let showBulkStatusSelect = $state(false);

	// Prune the selection to ids still visible whenever the list refetches
	// (status chip, search, advanced filter, modal-close re-apply, load-more).
	// Otherwise `selected` retains ids that fell off the list — inflating the
	// bulk-bar count and feeding invisible ids into bulk delete/status/export
	// (the same guard the exceptions queue applies). `pruneSelection` returns the
	// same Set when nothing went stale, so the guarded reassignment never loops.
	$effect(() => {
		const pruned = pruneSelection(
			selected,
			invoiceStore.all.map((inv) => inv.id)
		);
		if (pruned !== selected) selected = pruned;
	});

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
			const res = (await api.post('/api/invoices/bulk/status', {
				ids: [...selected],
				status: bulkStatusValue,
			})) as { updated: number; skipped: { id: string; reason: string }[] };
			await invoiceStore.fetch(buildParams());
			await invoiceStore.fetchCounts();
			selected = new Set();
			showBulkStatusSelect = false;
			// A skip can be an immutable status, but it can just as easily be a
			// segregation-of-duties or CFO-threshold refusal — an authorization
			// decision, not a data problem. Surface the backend's own reason(s)
			// rather than a single hardcoded label that misrepresents every
			// non-immutable skip.
			let msg: string;
			if (res.skipped?.length) {
				const uniqueReasons = [...new Set(res.skipped.map((s) => s.reason))];
				const reasonText =
					uniqueReasons.length === 1 ? uniqueReasons[0] : `${uniqueReasons.length} different reasons`;
				msg = `Updated ${res.updated}, skipped ${res.skipped.length} (${reasonText})`;
			} else {
				msg = `Updated ${res.updated} invoice(s)`;
			}
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
			// Through the shared client (`api.downloadBlobPost`), not a hand-rolled
			// fetch: this used to build its own request because `api.downloadBlob`
			// is GET-only, and in doing so it dropped `X-Entity-ID` — exporting
			// unscoped rows out of a list the user had scoped to one subsidiary —
			// and the 401 clear-and-bounce, so an expired session produced a
			// failure toast instead of a re-login.
			const blob = await api.downloadBlobPost('/api/invoices/bulk/export', {
				ids: [...selected],
				format,
			});
			if (format === 'json') {
				// Pretty-print the downloaded file. The transport helper stays
				// format-agnostic (a JSON export is still a Blob); how the saved
				// file reads is this page's call, so the re-serialize lives here.
				const data = JSON.parse(await blob.text());
				triggerDownload(
					new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
					`invoices-export.json`
				);
			} else {
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
		if (confirmDeleteId && !target.closest('.row-action')) {
			confirmDeleteId = null;
		}
		if (confirmBulkDelete && !target.closest('.bulk-delete-btn')) {
			confirmBulkDelete = false;
		}
		if (showBulkStatusSelect && !target.closest('.bulk-status-wrapper')) {
			showBulkStatusSelect = false;
		}
	}

</script>

<svelte:window onclick={handleWindowClick} />

<PageHeader title={m('invoices.title')}>
	{#snippet actions()}
		<input type="file" accept=".pdf,.png,.jpg,.jpeg,.tiff" multiple bind:this={fileInput} onchange={handleUpload} hidden />
		{#if auth.isAdmin}
			<button class="btn-secondary" onclick={() => (showBulkRecode = true)}>
				{m('invoices.action.bulkRecode')}
			</button>
		{/if}
		{#if auth.hasAnyRole('admin', 'ap_manager', 'cfo')}
			<button class="btn-secondary" onclick={() => (showCreate = true)}>
				{m('invoices.action.create')}
			</button>
		{/if}
		<!-- Same gate as Create above, because it is the same capability: both
		     create an invoice, and `POST /api/invoices/upload` is
		     `require_roles(ADMIN, AP_MANAGER, CFO)` exactly like `POST
		     /api/invoices`. Ungated, a clerk (who reaches this page — /invoices
		     carries no `roles` in nav.ts) picked files and watched every one
		     fail. -->
		{#if auth.hasAnyRole('admin', 'ap_manager', 'cfo')}
			<button class="btn-upload" disabled={uploading} onclick={() => fileInput.click()}>
				{uploading ? uploadProgress || m('invoices.action.uploading') : m('invoices.action.upload')}
			</button>
		{/if}
	{/snippet}

	<div class="filter-row">
		<div class="search-group">
			<SearchBox
				bind:value={search}
				placeholder={m('invoices.search.placeholder')}
				ariaLabel={m('invoices.search.aria')}
			/>
			<button
				class="advanced-btn"
				class:has-filters={hasAdvancedFilters}
				onclick={() => { advancedFilters = { ...advancedFilters, statuses: [...activeStatuses] }; showAdvancedSearch = true; }}
				aria-label={m('invoices.search.advanced')}
			>
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
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
		<nav class="filters">
			<button class="filter-chip" class:active={activeStatuses.length === 0} onclick={() => (activeStatuses = [])}>
				{m('common.all')} <span class="count">{totalCount}</span>
			</button>
			{#each chipStatuses as s}
				<button class="filter-chip" class:active={activeStatuses.includes(s)} onclick={() => toggleStatus(s)}>
					{STATUS_LABELS[s]} <span class="count">{statusCount(s)}</span>
				</button>
			{/each}
		</nav>
	</div>

	{#if selected.size > 0}
		<div class="bulk-bar">
			<span class="bulk-count">{m('invoices.bulk.selected', { n: selected.size })}</span>
			<button class="bulk-clear" onclick={() => (selected = new Set())}>{m('common.clear')}</button>
			<div class="bulk-divider"></div>

			{#if !auth.isClerkOnly}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<span class="bulk-btn-wrap" title={hasImmutableSelected ? m('invoices.bulk.cannotDelete') : ''}>
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
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
							{m('invoices.bulk.confirmDelete')}
						{:else}
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
							{m('invoices.bulk.delete')}
						{/if}
					</button>
				</span>

				<div class="bulk-status-wrapper">
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<span class="bulk-btn-wrap" title={validBulkTransitions.length === 0 ? m('invoices.bulk.noTransitions') : ''}>
						<button
							class="bulk-action-btn"
							disabled={bulkBusy || validBulkTransitions.length === 0}
							onclick={(e) => { e.stopPropagation(); showBulkStatusSelect = !showBulkStatusSelect; }}
						>
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
							{m('invoices.bulk.changeStatus')}
						</button>
					</span>
					{#if showBulkStatusSelect && validBulkTransitions.length > 0}
						<div class="bulk-status-dropdown">
							<select bind:value={bulkStatusValue} aria-label={m('invoices.bulk.newStatusAria')}>
								{#each validBulkTransitions as s}
									<option value={s}>{STATUS_LABELS[s]}</option>
								{/each}
							</select>
							<button class="bulk-apply-btn" disabled={bulkBusy} onclick={bulkStatusChange}>{m('common.apply')}</button>
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

	<DataTable isEmpty={invoiceStore.all.length === 0} empty={emptyMessage} colspan={9} fixed stickyHeader>
		{#snippet header()}
			<tr>
				<th class="checkbox-col"><input type="checkbox" aria-label={m('invoices.selectAllAria')} checked={allSelected} onchange={toggleSelectAll} /></th>
				<th>{m('invoices.col.invoiceNumber')}</th>
				<th>{m('invoices.col.vendor')}</th>
				<th>{m('invoices.col.description')}</th>
				<th>{m('invoices.col.poNumber')}</th>
				<th class="right">{m('invoices.col.amount')}</th>
				<th>{m('invoices.col.dueDate')}</th>
				<th>{m('invoices.col.status')}</th>
				<th class="actions-col"></th>
			</tr>
		{/snippet}
		{#snippet body()}
			{#each invoiceStore.all as invoice (invoice.id)}
				<tr
					class="clickable"
					class:row-selected={selected.has(invoice.id)}
					onclick={(e) => {
						if (isRowOpenClick(e)) editing = invoice;
					}}
				>
					<td class="checkbox-col" title={SYSTEM_MANAGED_STATUSES.has(invoice.status) ? `Cannot select — ${STATUS_LABELS[invoice.status]} is system-managed` : ''}><input type="checkbox" aria-label={`Select invoice ${invoice.invoice_number}`} checked={selected.has(invoice.id)} disabled={SYSTEM_MANAGED_STATUSES.has(invoice.status)} onchange={() => toggleSelect(invoice.id)} /></td>
					<td class="mono">
						<RowLink
							onclick={() => (editing = invoice)}
							ariaLabel={`Edit invoice ${invoice.invoice_number || 'draft'}`}
						>
							{invoice.invoice_number || '—'}
						</RowLink>
						{#if invoice.warnings?.length}
							<span
								class="warning-icon"
								role="img"
								aria-label={`Warnings: ${invoice.warnings.map((w) => w.message).join(', ')}`}
								title={invoice.warnings.map((w) => w.message).join(', ')}
							>
								<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
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
					<td class="right mono"><Money amount={invoice.amount} currency={invoice.currency} /></td>
					<td>{invoice.due_date}</td>
					<td><StatusBadge status={invoice.status} /></td>
					<td class="actions">
						{#if !auth.isClerkOnly && !IMMUTABLE_STATUSES.has(invoice.status)}
							<RowAction
								variant="danger"
								armed={confirmDeleteId === invoice.id}
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
								{deletingId === invoice.id
									? '…'
									: confirmDeleteId === invoice.id
										? m('invoices.row.confirm')
										: m('invoices.row.delete')}
							</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if invoiceStore.hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={() => invoiceStore.loadMore()} disabled={invoiceStore.loading}>
				{invoiceStore.loading ? m('common.loading') : m('invoices.loadMore', { shown: invoiceStore.all.length, total: invoiceStore.total })}
			</button>
		</div>
	{:else if invoiceStore.total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('invoices.showingAll', { total: invoiceStore.total })}</span>
		</div>
	{/if}
</PageHeader>

{#if editing}
	<InvoiceModal
		invoice={editing}
		onclose={closeInvoiceModal}
		onrefresh={refreshInvoiceList}
		activeSteps={workflowStore.activeSteps}
	/>
{/if}

{#if showAdvancedSearch}
	<AdvancedSearchModal
		filters={advancedFilters}
		onclose={() => (showAdvancedSearch = false)}
		onapply={(f) => { advancedFilters = f; activeStatuses = [...f.statuses]; }}
	/>
{/if}

{#if showBulkRecode}
	<BulkRecodeGLModal
		onclose={() => (showBulkRecode = false)}
		onapplied={() => {
			invoiceStore.fetch(buildParams()).catch(() => {});
			invoiceStore.fetchCounts().catch(() => {});
		}}
	/>
{/if}

{#if showCreate}
	<CreateInvoiceModal onclose={() => (showCreate = false)} onsaved={handleInvoiceCreated} />
{/if}

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */

	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}

	.search-group {
		display: flex;
		align-items: center;
		gap: 6px;
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

	/* Fixed column widths pair with DataTable's `fixed`/`stickyHeader` props.
	   These <th> widths apply because the header row is rendered from this
	   page's snippet (page CSS scope). */
	th:nth-child(1) { width: 40px; }
	th:nth-child(2) { width: 11%; }
	th:nth-child(3) { width: 16%; }
	th:nth-child(5) { width: 8%; }
	th:nth-child(6) { width: 9%; }
	th:nth-child(7) { width: 9%; }
	th:nth-child(8) { width: 15%; }
	th:nth-child(9) { width: 170px; }

	td {
		white-space: nowrap;
		vertical-align: middle;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	tr:last-child td {
		border-bottom: none;
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
		background: var(--accent-tint);
		color: var(--accent-on-tint);
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
		background: var(--accent-strong);
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

	.btn-secondary {
		padding: 8px 16px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
		flex-shrink: 0;
	}

	.btn-secondary:hover {
		border-color: var(--accent);
		color: var(--accent);
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
		border-color: var(--danger);
		color: var(--danger);
	}

	.bulk-delete-btn.armed {
		border-color: var(--danger);
		background: rgba(224, 64, 64, 0.1);
		color: var(--danger);
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
		/* base look (border/radius/colour/font/chevron) from the global recipe */
		padding: 5px 30px 5px 8px;
		font-size: 0.82rem;
	}

	.bulk-apply-btn {
		padding: 5px 12px;
		border-radius: 4px;
		border: none;
		background: var(--accent-strong);
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
