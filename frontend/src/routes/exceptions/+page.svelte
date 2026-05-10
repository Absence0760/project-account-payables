<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/components/Toast.svelte';
	import RowAction from '$lib/components/RowAction.svelte';
	import BulkBar from '$lib/components/BulkBar.svelte';

	interface ExceptionItem {
		id: string;
		invoice_id: string;
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

	let exceptions = $state<ExceptionItem[]>([]);
	let summary = $state<Summary | null>(null);
	let statusFilter = $state('open');
	let typeFilter = $state<string | null>(null);
	let selectedIds = $state<Set<string>>(new Set());

	let resolveTarget = $state<ExceptionItem | null>(null); // single-row resolve modal
	let bulkResolveOpen = $state(false);                    // bulk-resolve modal
	let resolutionText = $state('');
	let saving = $state(false);

	const TYPE_COLORS: Record<string, string> = {
		duplicate: '#8b5cf6',
		po_mismatch: '#d4940a',
		fraud_flag: '#e04040',
		extraction_failed: '#e04040',
		unverified_vendor: '#d4940a',
		review_rejected: '#e04040',
		amount_exceeded: '#e04040',
		missing_data: '#d4940a',
	};

	const SEVERITY_COLORS: Record<string, string> = {
		error: '#e04040',
		warning: '#d4940a',
		info: '#638cff',
	};

	$effect(() => {
		statusFilter;
		typeFilter;
		loadExceptions();
	});

	$effect(() => {
		loadSummary();
	});

	async function loadExceptions() {
		try {
			const params = new URLSearchParams();
			if (statusFilter !== 'all') params.set('status', statusFilter);
			if (typeFilter) params.set('type', typeFilter);
			const data = await api.get<{ items: ExceptionItem[] }>(`/api/exceptions?${params}`);
			exceptions = data.items;
			// Drop selections for ids that fell off the list.
			const visible = new Set(data.items.map((e) => e.id));
			selectedIds = new Set([...selectedIds].filter((id) => visible.has(id)));
		} catch {
			toast('Failed to load exceptions', 'error');
		}
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

	function formatDate(iso: string | null): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			hour: 'numeric',
			minute: '2-digit',
		});
	}

	function formatCurrency(n: number | null): string {
		if (n === null) return '—';
		return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
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
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Exceptions</h1>
	</header>

	{#if summary}
		<nav class="filters">
			<button
				class="filter-chip"
				class:active={statusFilter === 'all'}
				onclick={() => (statusFilter = 'all')}
			>
				All <span class="count">{summary.open + summary.escalated + summary.resolved + summary.dismissed}</span>
			</button>
			<button
				class="filter-chip"
				class:active={statusFilter === 'open'}
				onclick={() => (statusFilter = 'open')}
			>
				Open <span class="count">{summary.open}</span>
			</button>
			<button
				class="filter-chip"
				class:active={statusFilter === 'escalated'}
				onclick={() => (statusFilter = 'escalated')}
			>
				Escalated <span class="count">{summary.escalated}</span>
			</button>
			<button
				class="filter-chip"
				class:active={statusFilter === 'resolved'}
				onclick={() => (statusFilter = 'resolved')}
			>
				Resolved <span class="count">{summary.resolved}</span>
			</button>
			<button
				class="filter-chip"
				class:active={statusFilter === 'dismissed'}
				onclick={() => (statusFilter = 'dismissed')}
			>
				Dismissed <span class="count">{summary.dismissed}</span>
			</button>
		</nav>

		{#if Object.keys(summary.by_type).length > 0}
			<nav class="type-filters">
				<button
					class="type-chip"
					class:active={typeFilter === null}
					onclick={() => (typeFilter = null)}
				>
					All types
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
				Resolve {selectedIds.size}
			</button>
		{/snippet}
	</BulkBar>

	<div class="grid-container">
		<table>
			<thead>
				<tr>
					<th class="checkbox-col">
						<input
							type="checkbox"
							checked={allSelected}
							onchange={toggleSelectAll}
							aria-label="Select all selectable exceptions"
						/>
					</th>
					<th>Type</th>
					<th>Sev</th>
					<th>Invoice</th>
					<th>Vendor</th>
					<th class="right">Amount</th>
					<th>Assignee</th>
					<th>Age</th>
					<th>Due</th>
					<th>Status</th>
					<th class="actions-col"></th>
				</tr>
			</thead>
			<tbody>
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
									aria-label="Select exception"
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
								<RowAction onclick={() => openResolve(exc)}>Resolve</RowAction>
								<RowAction href="/invoices?id={exc.invoice_id}">Invoice</RowAction>
							{:else}
								<RowAction href="/invoices?id={exc.invoice_id}">Invoice</RowAction>
							{/if}
						</td>
					</tr>
				{:else}
					<tr>
						<td colspan="11" class="empty">
							{#if statusFilter === 'open'}
								No open exceptions. Everything looks good!
							{:else}
								No exceptions found.
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>

<!-- Single-row resolve modal -->
{#if resolveTarget}
	<div
		class="backdrop"
		onclick={(e) => { if (e.target === e.currentTarget) (resolveTarget = null); }}
	>
		<div class="modal" role="dialog" aria-label="Resolve exception">
			<h2>Resolve exception</h2>
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
					<span>Resolution note</span>
					<input
						type="text"
						bind:value={resolutionText}
						placeholder="What did you do?"
						maxlength="500"
						autofocus
					/>
				</label>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={() => (resolveTarget = null)}>
						Cancel
					</button>
					<button
						type="button"
						class="btn-secondary"
						disabled={saving}
						onclick={() => commitResolve('dismiss')}
					>
						Dismiss
					</button>
					<button
						type="button"
						class="btn-warning"
						disabled={saving || !resolutionText.trim()}
						onclick={() => commitResolve('escalate')}
					>
						Escalate
					</button>
					<button type="submit" class="btn-primary" disabled={saving || !resolutionText.trim()}>
						{saving ? 'Saving…' : 'Resolve'}
					</button>
				</div>
			</form>
		</div>
	</div>
{/if}

<!-- Bulk-resolve modal -->
{#if bulkResolveOpen}
	<div
		class="backdrop"
		onclick={(e) => { if (e.target === e.currentTarget) (bulkResolveOpen = false); }}
	>
		<div class="modal" role="dialog" aria-label="Resolve selected exceptions">
			<h2>Resolve {selectedIds.size} exception{selectedIds.size === 1 ? '' : 's'}</h2>
			<p class="modal-hint">
				All selected rows will receive the same resolution note. Rows already in a terminal
				state are silently skipped server-side.
			</p>
			<form onsubmit={(e) => { e.preventDefault(); commitBulkResolve('resolve'); }}>
				<label>
					<span>Resolution note</span>
					<input
						type="text"
						bind:value={resolutionText}
						placeholder="Applied to every selected row"
						maxlength="500"
						autofocus
					/>
				</label>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={() => (bulkResolveOpen = false)}>
						Cancel
					</button>
					<button
						type="button"
						class="btn-secondary"
						disabled={saving}
						onclick={() => commitBulkResolve('dismiss')}
					>
						Dismiss
					</button>
					<button
						type="button"
						class="btn-warning"
						disabled={saving || !resolutionText.trim()}
						onclick={() => commitBulkResolve('escalate')}
					>
						Escalate
					</button>
					<button type="submit" class="btn-primary" disabled={saving || !resolutionText.trim()}>
						{saving ? 'Saving…' : 'Resolve'}
					</button>
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
		gap: 16px;
	}

	.toolbar h1 {
		margin: 0;
		font-size: 1.4rem;
		font-weight: 600;
		color: var(--text);
	}

	/* --- Status filter chips --- */

	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.filter-chip {
		display: inline-flex;
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

	/* --- Grid --- */

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
		font-size: 0.72rem;
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
		vertical-align: middle;
	}

	tr:last-child td {
		border-bottom: none;
	}

	tbody tr:hover {
		background: rgba(99, 140, 255, 0.04);
	}

	tbody tr.row-selected {
		background: rgba(99, 140, 255, 0.08);
	}

	tbody tr.resolved td {
		opacity: 0.55;
	}

	.checkbox-col {
		width: 32px;
		padding-right: 0;
	}

	.right {
		text-align: right;
	}

	.mono {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
	}

	.muted-cell {
		color: var(--text-muted);
	}

	.muted-cell.overdue {
		color: #e04040;
		font-weight: 600;
	}

	.empty {
		text-align: center;
		padding: 40px 14px;
		color: var(--text-muted);
		font-style: italic;
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
		color: #e04040;
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

	.actions {
		display: flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
	}

	/* --- Modal --- */

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
		width: min(520px, 92vw);
		padding: 24px;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.3);
	}

	.modal h2 {
		margin: 0 0 4px;
		font-size: 1.1rem;
		font-weight: 600;
		color: var(--text);
	}

	.modal-hint {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0 0 8px;
	}

	.modal-description {
		font-size: 0.82rem;
		color: var(--text);
		margin: 0 0 14px;
		padding: 8px 10px;
		background: var(--bg);
		border-radius: 4px;
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

	.modal input[type='text'] {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.88rem;
		color: var(--text);
		font-family: inherit;
		width: 100%;
		box-sizing: border-box;
	}

	.modal input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
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
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-cancel:hover {
		background: var(--bg);
		color: var(--text);
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

	.btn-primary {
		padding: 8px 18px;
		border-radius: 4px;
		border: 1px solid var(--accent);
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-primary:hover:not(:disabled) {
		filter: brightness(1.1);
	}

	.btn-primary:disabled,
	.btn-secondary:disabled,
	.btn-warning:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
</style>
