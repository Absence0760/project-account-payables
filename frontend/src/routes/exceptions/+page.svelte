<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import BulkBar from '$lib/components/ui/BulkBar.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Tabs from '$lib/components/ui/Tabs.svelte';
	import AgentDashboard from '$lib/components/exceptions/AgentDashboard.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import { m } from '$lib/i18n/store.svelte';

	interface ExceptionItem {
		id: string;
		invoice_id: string | null;
		invoice_number: string | null;
		vendor_name: string | null;
		amount: number | null;
		exception_type: string;
		type_label: string;
		severity: string;
		description: string | null;
		status: string;
		resolution: string | null;
		resolved_by: string | null;
		resolved_at: string | null;
		assigned_to: string | null;
		assigned_to_user_id: string | null;
		due_at: string | null;
		is_overdue: boolean;
		time_to_resolution_hours: number | null;
		created_at: string;
	}

	interface Summary {
		open: number;
		escalated: number;
		resolved: number;
		dismissed: number;
		by_type: Record<string, number>;
	}

	type Action = 'resolve' | 'escalate' | 'dismiss';

	const PAGE_SIZE = 20;
	let exceptions = $state<ExceptionItem[]>([]);
	let total = $state(0);
	let page = $state(1);
	let loadingMore = $state(false);
	let summary = $state<Summary | null>(null);
	let statusFilter = $state('open');
	let typeFilter = $state<string | null>(null);
	let selectedIds = $state<Set<string>>(new Set());

	let hasMore = $derived(exceptions.length < total);

	// Top-level view: the operational queue vs the AI-agent dashboard. Persisted
	// in the URL hash so a refresh / shared link keeps the tab.
	let view = $state<'queue' | 'agents'>('queue');

	let resolveTarget = $state<ExceptionItem | null>(null); // single-row resolve modal
	let bulkResolveOpen = $state(false);                    // bulk-resolve modal
	let resolutionText = $state('');
	let saving = $state(false);

	// Badge text sits on a 12%-tint-of-itself background, so the tone must
	// clear WCAG 1.4.3 (4.5:1) on that tint — #e04040 (3.68) and #8b5cf6
	// (3.60) fail; their lighter siblings #f06464 (4.76) / #a78bfa (5.27)
	// pass. Amber #d4940a (5.51) already passes.
	const TYPE_COLORS: Record<string, string> = {
		duplicate: '#a78bfa',
		po_mismatch: '#d4940a',
		fraud_flag: '#f06464',
		extraction_failed: '#f06464',
		unverified_vendor: '#d4940a',
		review_rejected: '#f06464',
		amount_exceeded: '#f06464',
		missing_data: '#d4940a',
		quality_hold: '#f06464',
	};

	const SEVERITY_COLORS: Record<string, string> = {
		error: '#f06464',
		warning: '#d4940a',
		info: '#638cff',
	};

	$effect(() => {
		statusFilter;
		typeFilter;
		loadExceptions();
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
		loadSummary();
	});

	async function loadExceptions(opts: { append?: boolean; nextPage?: number } = {}) {
		const nextPage = opts.nextPage ?? 1;
		if (opts.append) loadingMore = true;
		try {
			const params = new URLSearchParams();
			if (statusFilter !== 'all') params.set('status', statusFilter);
			if (typeFilter) params.set('type', typeFilter);
			params.set('page', String(nextPage));
			params.set('page_size', String(PAGE_SIZE));
			const data = await api.get<{ items: ExceptionItem[]; total: number }>(
				`/api/exceptions?${params}`
			);
			exceptions = opts.append ? [...exceptions, ...data.items] : data.items;
			total = data.total;
			page = nextPage;
			// Drop selections for ids that fell off the list.
			const visible = new Set(exceptions.map((e) => e.id));
			selectedIds = new Set([...selectedIds].filter((id) => visible.has(id)));
		} catch {
			toast('Failed to load exceptions', 'error');
		} finally {
			loadingMore = false;
		}
	}

	async function loadMoreExceptions() {
		await loadExceptions({ append: true, nextPage: page + 1 });
	}

	async function loadSummary() {
		try {
			summary = await api.get<Summary>('/api/exceptions/summary');
		} catch {
			/* non-critical */
		}
	}

	function openResolve(exc: ExceptionItem) {
		resolveTarget = exc;
		resolutionText = '';
	}

	function openBulkResolve() {
		bulkResolveOpen = true;
		resolutionText = '';
	}

	async function commitResolve(action: Action) {
		if (!resolveTarget) return;
		const note = resolutionText.trim();
		if (!note && action !== 'dismiss') {
			toast('Resolution note is required', 'error');
			return;
		}
		saving = true;
		try {
			await api.post(`/api/exceptions/${resolveTarget.id}/resolve`, {
				resolution: note || `${action}d by user`,
				action,
			});
			toast(`Exception ${action}d`, 'success');
			resolveTarget = null;
			resolutionText = '';
			await Promise.all([loadExceptions(), loadSummary()]);
		} catch (err) {
			toast(extractError(err), 'error');
		} finally {
			saving = false;
		}
	}

	async function commitBulkResolve(action: Action) {
		const ids = [...selectedIds];
		if (ids.length === 0) return;
		const note = resolutionText.trim();
		if (!note && action !== 'dismiss') {
			toast('Resolution note is required', 'error');
			return;
		}
		saving = true;
		try {
			const body = await api.post<{ updated: number; skipped: { id: string; reason: string }[] }>(
				'/api/exceptions/bulk/resolve',
				{ ids, action, resolution: note || `bulk ${action}` }
			);
			const skipped = body.skipped.length;
			toast(
				skipped === 0
					? `${body.updated} ${action}d`
					: `${body.updated} ${action}d, ${skipped} skipped`,
				skipped === 0 ? 'success' : 'info'
			);
			bulkResolveOpen = false;
			resolutionText = '';
			selectedIds = new Set();
			await Promise.all([loadExceptions(), loadSummary()]);
		} catch (err) {
			toast(extractError(err), 'error');
		} finally {
			saving = false;
		}
	}

	function extractError(err: unknown): string {
		const e = err as { detail?: string; message?: string } | null;
		return e?.detail ?? e?.message ?? 'Action failed';
	}

	function toggleSelect(id: string) {
		const next = new Set(selectedIds);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		selectedIds = next;
	}

	let selectableIds = $derived(
		new Set(
			exceptions
				.filter((e) => e.status === 'open' || e.status === 'escalated')
				.map((e) => e.id)
		)
	);

	let allSelected = $derived(
		selectableIds.size > 0 && [...selectableIds].every((id) => selectedIds.has(id))
	);

	function toggleSelectAll() {
		if (allSelected) selectedIds = new Set();
		else selectedIds = new Set(selectableIds);
	}

	function formatCurrency(n: number | null): string {
		return formatMoney(n, { currency: orgCurrency.currency });
	}

	function timeAgo(iso: string): string {
		const diff = Date.now() - new Date(iso).getTime();
		const hours = Math.floor(diff / 3600000);
		if (hours < 1) return 'Just now';
		if (hours < 24) return `${hours}h ago`;
		const days = Math.floor(hours / 24);
		if (days === 1) return '1d ago';
		return `${days}d ago`;
	}

	function dueLabel(exc: ExceptionItem): string {
		if (!exc.due_at) return '—';
		const diff = new Date(exc.due_at).getTime() - Date.now();
		const hours = Math.round(diff / 3600000);
		if (hours <= 0) return `${Math.abs(hours)}h overdue`;
		if (hours < 24) return `in ${hours}h`;
		return `in ${Math.round(hours / 24)}d`;
	}

	let COLUMNS = $derived([
		{ class: 'checkbox-col' },
		{ label: m('exceptions.col.type') },
		{ label: m('exceptions.col.severity') },
		{ label: m('exceptions.col.invoice') },
		{ label: m('exceptions.col.vendor') },
		{ label: m('exceptions.col.amount'), class: 'right' },
		{ label: m('exceptions.col.assignee') },
		{ label: m('exceptions.col.age') },
		{ label: m('exceptions.col.due') },
		{ label: m('exceptions.col.status') },
		{ class: 'actions-col' }
	]);

	let statusChips = $derived(
		summary
			? [
					{
						key: 'all',
						label: m('common.all'),
						count: summary.open + summary.escalated + summary.resolved + summary.dismissed
					},
					{ key: 'open', label: m('exceptions.filter.open'), count: summary.open },
					{ key: 'escalated', label: m('exceptions.filter.escalated'), count: summary.escalated },
					{ key: 'resolved', label: m('exceptions.filter.resolved'), count: summary.resolved },
					{ key: 'dismissed', label: m('exceptions.filter.dismissed'), count: summary.dismissed }
				]
			: []
	);

	let emptyMessage = $derived(
		statusFilter === 'open'
			? m('exceptions.empty.open')
			: m('exceptions.empty.other')
	);
</script>

<PageHeader title={m('exceptions.title')}>
	<Tabs
		tabs={[
			{ key: 'queue', label: m('exceptions.tab.queue'), count: summary ? summary.open + summary.escalated : undefined },
			{ key: 'agents', label: m('exceptions.tab.agents') }
		]}
		bind:active={view}
		ariaLabel="Exceptions views"
		idPrefix="exc"
	/>

	{#if view === 'agents'}
		<div id="exc-panel-agents" role="tabpanel" aria-labelledby="exc-tab-agents">
			<AgentDashboard />
		</div>
	{:else}
	<div id="exc-panel-queue" role="tabpanel" aria-labelledby="exc-tab-queue">
	{#if summary}
		<FilterChips chips={statusChips} bind:active={statusFilter} />

		{#if Object.keys(summary.by_type).length > 0}
			<nav class="type-filters">
				<button
					class="type-chip"
					class:active={typeFilter === null}
					onclick={() => (typeFilter = null)}
				>
					{m('exceptions.filter.allTypes')}
				</button>
				{#each Object.entries(summary.by_type) as [type, count]}
					<button
						class="type-chip"
						class:active={typeFilter === type}
						style="--type-color:{TYPE_COLORS[type] ?? '#888'}"
						onclick={() => (typeFilter = typeFilter === type ? null : type)}
					>
						<span class="type-dot"></span>
						{type.replace(/_/g, ' ')} <span class="count">{count}</span>
					</button>
				{/each}
			</nav>
		{/if}
	{/if}

	<BulkBar count={selectedIds.size} onclear={() => (selectedIds = new Set())}>
		{#snippet actions()}
			<button class="bulk-action-btn" onclick={openBulkResolve}>
				{m('exceptions.bulk.resolve', { n: selectedIds.size })}
			</button>
		{/snippet}
	</BulkBar>

	<DataTable columns={COLUMNS} isEmpty={exceptions.length === 0} empty={emptyMessage} colspan={11}>
		{#snippet header()}
			<tr>
				<th class="checkbox-col">
					<input
						type="checkbox"
						checked={allSelected}
						onchange={toggleSelectAll}
						aria-label={m('exceptions.selectAllAria')}
					/>
				</th>
				<th>{m('exceptions.col.type')}</th>
				<th>{m('exceptions.col.severity')}</th>
				<th>{m('exceptions.col.invoice')}</th>
				<th>{m('exceptions.col.vendor')}</th>
				<th class="right">{m('exceptions.col.amount')}</th>
				<th>{m('exceptions.col.assignee')}</th>
				<th>{m('exceptions.col.age')}</th>
				<th>{m('exceptions.col.due')}</th>
				<th>{m('exceptions.col.status')}</th>
				<th class="actions-col"></th>
			</tr>
		{/snippet}
		{#snippet body()}
			{#each exceptions as exc (exc.id)}
				<tr
					class:row-selected={selectedIds.has(exc.id)}
					class:resolved={exc.status === 'resolved' || exc.status === 'dismissed'}
				>
					<td class="checkbox-col">
						{#if selectableIds.has(exc.id)}
							<input
								type="checkbox"
								checked={selectedIds.has(exc.id)}
								onchange={() => toggleSelect(exc.id)}
								aria-label={m('exceptions.selectAria')}
							/>
						{/if}
					</td>
					<td>
						<span
							class="type-badge"
							style="background:{TYPE_COLORS[exc.exception_type] ?? '#888'}1f;color:{TYPE_COLORS[exc.exception_type] ?? '#888'}"
							title={exc.description ?? ''}
						>
							{exc.type_label}
						</span>
					</td>
					<td>
						<span
							class="severity"
							style="color:{SEVERITY_COLORS[exc.severity] ?? '#888'}"
						>
							{exc.severity}
						</span>
					</td>
					<td class="mono">{exc.invoice_number ?? '—'}</td>
					<td class="muted-cell">{exc.vendor_name ?? '—'}</td>
					<td class="mono right">{formatCurrency(exc.amount)}</td>
					<td class="muted-cell">{exc.assigned_to ?? '—'}</td>
					<td class="muted-cell" title={exc.created_at}>{timeAgo(exc.created_at)}</td>
					<td class="muted-cell" class:overdue={exc.is_overdue}>
						{dueLabel(exc)}
					</td>
					<td>
						<span class="status-badge badge-{exc.status}">{exc.status}</span>
					</td>
					<td class="actions">
						{#if exc.status === 'open' || exc.status === 'escalated'}
							<RowAction onclick={() => openResolve(exc)}>{m('exceptions.row.resolve')}</RowAction>
						{/if}
						{#if exc.invoice_id}
							<RowAction href="/invoices?id={exc.invoice_id}">{m('exceptions.row.invoice')}</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMoreExceptions} disabled={loadingMore}>
				{loadingMore ? m('common.loading') : m('exceptions.loadMore', { shown: exceptions.length, total })}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('exceptions.showingAll', { total })}</span>
		</div>
	{/if}
	</div>
	{/if}
</PageHeader>

<!-- Single-row resolve modal -->
<Modal
	open={resolveTarget !== null}
	ariaLabel="Resolve exception"
	width="sm"
	onclose={() => (resolveTarget = null)}
>
	{#if resolveTarget}
		<h2>{m('exceptions.resolveModal.title')}</h2>
		<p class="modal-hint">
			<strong>{resolveTarget.type_label}</strong>
			{#if resolveTarget.invoice_number}— {resolveTarget.invoice_number}{/if}
			{#if resolveTarget.vendor_name}· {resolveTarget.vendor_name}{/if}
		</p>
		{#if resolveTarget.description}
			<p class="modal-description">{resolveTarget.description}</p>
		{/if}
		<form onsubmit={(e) => { e.preventDefault(); commitResolve('resolve'); }}>
			<label>
				<span>{m('exceptions.resolveModal.note')}</span>
				<input
					type="text"
					bind:value={resolutionText}
					placeholder={m('exceptions.resolveModal.notePlaceholder')}
					maxlength="500"
					autofocus
				/>
			</label>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (resolveTarget = null)}>
					{m('common.cancel')}
				</button>
				<button
					type="button"
					class="btn-secondary"
					disabled={saving}
					onclick={() => commitResolve('dismiss')}
				>
					{m('exceptions.resolveModal.dismiss')}
				</button>
				<button
					type="button"
					class="btn-warning"
					disabled={saving || !resolutionText.trim()}
					onclick={() => commitResolve('escalate')}
				>
					{m('exceptions.resolveModal.escalate')}
				</button>
				<button type="submit" class="btn-primary" disabled={saving || !resolutionText.trim()}>
					{saving ? m('common.saving') : m('exceptions.resolveModal.resolve')}
				</button>
			</div>
		</form>
	{/if}
</Modal>

<!-- Bulk-resolve modal -->
<Modal
	open={bulkResolveOpen}
	ariaLabel="Resolve selected exceptions"
	width="sm"
	onclose={() => (bulkResolveOpen = false)}
>
	<h2>{m('exceptions.bulkModal.title', { n: selectedIds.size })}</h2>
	<p class="modal-hint">
		{m('exceptions.bulkModal.hint')}
	</p>
	<form onsubmit={(e) => { e.preventDefault(); commitBulkResolve('resolve'); }}>
		<label>
			<span>{m('exceptions.resolveModal.note')}</span>
			<input
				type="text"
				bind:value={resolutionText}
				placeholder={m('exceptions.bulkModal.notePlaceholder')}
				maxlength="500"
				autofocus
			/>
		</label>
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (bulkResolveOpen = false)}>
				{m('common.cancel')}
			</button>
			<button
				type="button"
				class="btn-secondary"
				disabled={saving}
				onclick={() => commitBulkResolve('dismiss')}
			>
				{m('exceptions.resolveModal.dismiss')}
			</button>
			<button
				type="button"
				class="btn-warning"
				disabled={saving || !resolutionText.trim()}
				onclick={() => commitBulkResolve('escalate')}
			>
				{m('exceptions.resolveModal.escalate')}
			</button>
			<button type="submit" class="btn-primary" disabled={saving || !resolutionText.trim()}>
				{saving ? m('common.saving') : m('exceptions.resolveModal.resolve')}
			</button>
		</div>
	</form>
</Modal>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */

	/* --- Type filter chips --- */

	.type-filters {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.type-chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 4px 10px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.78rem;
		text-transform: capitalize;
		cursor: pointer;
		font-family: inherit;
	}

	.type-chip:hover {
		color: var(--text);
	}

	.type-chip.active {
		border-color: var(--type-color, var(--accent));
		color: var(--text);
	}

	.type-dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--type-color, var(--accent));
	}

	.type-chip .count {
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	/* --- Bulk-bar action --- */

	.bulk-action-btn {
		padding: 6px 14px;
		border-radius: 4px;
		border: 1px solid var(--accent);
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.bulk-action-btn:hover {
		filter: brightness(1.1);
	}

	/* --- Bespoke cells / rows --- */

	/* De-emphasize resolved/dismissed rows with a tint, not a blanket
	   opacity — opacity composites every cell's text below the WCAG 1.4.3
	   4.5:1 contrast floor. The resolved/dismissed status badge carries the
	   state signal. */
	tbody tr.resolved td {
		background: rgba(138, 143, 160, 0.05);
	}

	.checkbox-col {
		width: 32px;
		padding-right: 0;
	}

	.muted-cell {
		color: var(--text-muted);
	}

	.muted-cell.overdue {
		color: #f06464;
		font-weight: 600;
	}

	/* --- Type / severity / status badges --- */

	.type-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.75rem;
		font-weight: 600;
		white-space: nowrap;
	}

	.severity {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
	}

	.status-badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: capitalize;
	}

	.badge-open {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}

	.badge-escalated {
		background: rgba(224, 64, 64, 0.12);
		color: #f06464;
	}

	.badge-resolved {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}

	.badge-dismissed {
		background: var(--bg);
		color: var(--text-muted);
	}

	.actions-col {
		width: 180px;
	}

	/* --- Modal extras --- */

	.modal-description {
		font-size: 0.82rem;
		color: var(--text);
		margin: 0 0 14px;
		padding: 8px 10px;
		background: var(--bg);
		border-radius: 4px;
	}

	.modal input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.btn-secondary {
		padding: 8px 14px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-secondary:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}

	.btn-warning {
		padding: 8px 14px;
		border-radius: 4px;
		border: 1px solid #d4940a;
		background: var(--surface);
		color: #d4940a;
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-warning:hover:not(:disabled) {
		background: rgba(212, 148, 10, 0.1);
	}

	.btn-secondary:disabled,
	.btn-warning:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
</style>
