<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/components/Toast.svelte';

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
		created_at: string;
	}

	interface Summary {
		open: number;
		escalated: number;
		resolved: number;
		dismissed: number;
		by_type: Record<string, number>;
	}

	let exceptions = $state<ExceptionItem[]>([]);
	let summary = $state<Summary | null>(null);
	let statusFilter = $state('open');
	let resolving = $state<string | null>(null);
	let resolutionText = $state('');
	let showResolveId = $state<string | null>(null);

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
		loadExceptions();
		loadSummary();
	});

	$effect(() => {
		statusFilter;
		loadExceptions();
	});

	async function loadExceptions() {
		try {
			const params = new URLSearchParams();
			if (statusFilter !== 'all') params.set('status', statusFilter);
			const data = await api.get<{ items: ExceptionItem[] }>(`/api/exceptions?${params}`);
			exceptions = data.items;
		} catch {
			toast('Failed to load exceptions', 'error');
		}
	}

	async function loadSummary() {
		try {
			summary = await api.get<Summary>('/api/exceptions/summary');
		} catch { /* non-critical */ }
	}

	async function handleAction(id: string, action: string) {
		if (!resolutionText.trim() && action !== 'dismiss') return;
		resolving = id;
		try {
			await api.post(`/api/exceptions/${id}/resolve`, {
				resolution: resolutionText.trim() || `${action}d by user`,
				action,
			});
			toast(`Exception ${action}d`, 'success');
			showResolveId = null;
			resolutionText = '';
			await loadExceptions();
			await loadSummary();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Action failed', 'error');
		} finally {
			resolving = null;
		}
	}

	function formatDate(iso: string): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
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
		if (days === 1) return '1 day ago';
		return `${days} days ago`;
	}
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Exceptions</h1>
		{#if summary}
			<div class="summary-chips">
				<span class="chip" class:active={statusFilter === 'open'} onclick={() => (statusFilter = 'open')}>
					Open <span class="chip-count">{summary.open}</span>
				</span>
				<span class="chip" class:active={statusFilter === 'escalated'} onclick={() => (statusFilter = 'escalated')}>
					Escalated <span class="chip-count">{summary.escalated}</span>
				</span>
				<span class="chip" class:active={statusFilter === 'resolved'} onclick={() => (statusFilter = 'resolved')}>
					Resolved <span class="chip-count">{summary.resolved}</span>
				</span>
				<span class="chip" class:active={statusFilter === 'all'} onclick={() => (statusFilter = 'all')}>
					All
				</span>
			</div>
		{/if}
	</header>

	{#if summary && summary.open > 0}
		<div class="type-summary">
			{#each Object.entries(summary.by_type) as [type, count]}
				<div class="type-chip" style="border-color:{TYPE_COLORS[type] ?? '#888'}">
					<span class="type-count" style="color:{TYPE_COLORS[type] ?? '#888'}">{count}</span>
					<span class="type-label">{type.replace(/_/g, ' ')}</span>
				</div>
			{/each}
		</div>
	{/if}

	<div class="exception-list">
		{#each exceptions as exc (exc.id)}
			<div class="exception-card" class:resolved={exc.status === 'resolved' || exc.status === 'dismissed'}>
				<div class="exc-header">
					<span class="exc-type" style="background:{TYPE_COLORS[exc.exception_type] ?? '#888'}20;color:{TYPE_COLORS[exc.exception_type] ?? '#888'}">
						{exc.type_label}
					</span>
					<span class="exc-severity" style="color:{SEVERITY_COLORS[exc.severity] ?? '#888'}">
						{exc.severity}
					</span>
					<span class="exc-age">{timeAgo(exc.created_at)}</span>
					<span class="exc-status badge-{exc.status}">{exc.status}</span>
				</div>

				<div class="exc-body">
					<div class="exc-invoice">
						<span class="exc-inv-number">{exc.invoice_number ?? '—'}</span>
						<span class="exc-vendor">{exc.vendor_name ?? '—'}</span>
						<span class="exc-amount">{formatCurrency(exc.amount)}</span>
					</div>
					<p class="exc-description">{exc.description ?? '—'}</p>
				</div>

				{#if exc.resolution}
					<div class="exc-resolution">
						<span class="resolution-label">Resolution:</span> {exc.resolution}
						{#if exc.resolved_by}
							<span class="resolution-by">— {exc.resolved_by}</span>
						{/if}
					</div>
				{/if}

				{#if exc.status === 'open' || exc.status === 'escalated'}
					{#if showResolveId === exc.id}
						<div class="resolve-form">
							<input
								type="text"
								class="resolve-input"
								placeholder="Resolution note..."
								bind:value={resolutionText}
							/>
							<div class="resolve-actions">
								<button class="btn-resolve" disabled={resolving === exc.id} onclick={() => handleAction(exc.id, 'resolve')}>
									{resolving === exc.id ? '...' : 'Resolve'}
								</button>
								<button class="btn-escalate" disabled={resolving === exc.id} onclick={() => handleAction(exc.id, 'escalate')}>
									Escalate
								</button>
								<button class="btn-dismiss" disabled={resolving === exc.id} onclick={() => handleAction(exc.id, 'dismiss')}>
									Dismiss
								</button>
								<button class="btn-cancel-sm" onclick={() => { showResolveId = null; resolutionText = ''; }}>
									Cancel
								</button>
							</div>
						</div>
					{:else}
						<div class="exc-actions">
							<button class="btn-action" onclick={() => { showResolveId = exc.id; resolutionText = ''; }}>
								Take Action
							</button>
							<a href="/invoices" class="btn-link">View Invoice</a>
						</div>
					{/if}
				{/if}
			</div>
		{:else}
			<div class="empty-state">
				{#if statusFilter === 'open'}
					<p>No open exceptions. Everything looks good!</p>
				{:else}
					<p>No exceptions found.</p>
				{/if}
			</div>
		{/each}
	</div>
</div>

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
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 12px;
	}

	h1 {
		font-size: 1.3rem;
		font-weight: 700;
		margin: 0;
	}

	.summary-chips {
		display: flex;
		gap: 6px;
	}

	.chip {
		padding: 5px 12px;
		border-radius: 16px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		display: flex;
		align-items: center;
		gap: 5px;
	}

	.chip:hover {
		border-color: var(--accent);
		color: var(--text);
	}

	.chip.active {
		background: var(--accent);
		color: #fff;
		border-color: var(--accent);
	}

	.chip-count {
		font-size: 0.72rem;
		font-weight: 600;
	}

	/* Type summary */
	.type-summary {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}

	.type-chip {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 6px 12px;
		border-radius: 6px;
		background: var(--surface);
		border: 1px solid;
		font-size: 0.82rem;
	}

	.type-count {
		font-weight: 700;
		font-size: 0.9rem;
	}

	.type-label {
		color: var(--text-muted);
		text-transform: capitalize;
	}

	/* Exception cards */
	.exception-list {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.exception-card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 16px 18px;
	}

	.exception-card.resolved {
		opacity: 0.6;
	}

	.exc-header {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-bottom: 10px;
	}

	.exc-type {
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.75rem;
		font-weight: 600;
	}

	.exc-severity {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
	}

	.exc-age {
		font-size: 0.75rem;
		color: var(--text-muted);
		margin-left: auto;
	}

	.exc-status {
		font-size: 0.72rem;
		font-weight: 600;
		padding: 2px 8px;
		border-radius: 8px;
		text-transform: capitalize;
	}

	.badge-open { background: rgba(212, 148, 10, 0.12); color: #d4940a; }
	.badge-escalated { background: rgba(224, 64, 64, 0.12); color: #e04040; }
	.badge-resolved { background: rgba(31, 168, 106, 0.12); color: #1fa86a; }
	.badge-dismissed { background: var(--bg); color: var(--text-muted); }

	.exc-body {
		margin-bottom: 8px;
	}

	.exc-invoice {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-bottom: 4px;
	}

	.exc-inv-number {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--text);
	}

	.exc-vendor {
		font-size: 0.85rem;
		color: var(--text-muted);
	}

	.exc-amount {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--text);
		margin-left: auto;
	}

	.exc-description {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0;
		line-height: 1.4;
	}

	.exc-resolution {
		font-size: 0.8rem;
		color: var(--text-muted);
		padding: 8px 10px;
		background: var(--bg);
		border-radius: 4px;
		margin-top: 8px;
	}

	.resolution-label {
		font-weight: 600;
		color: var(--text);
	}

	.resolution-by {
		font-style: italic;
	}

	/* Actions */
	.exc-actions {
		display: flex;
		gap: 8px;
		margin-top: 10px;
	}

	.btn-action {
		padding: 5px 14px;
		border-radius: 4px;
		border: 1px solid var(--accent);
		background: var(--surface);
		color: var(--accent);
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-action:hover {
		background: rgba(99, 140, 255, 0.08);
	}

	.btn-link {
		padding: 5px 14px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		cursor: pointer;
		font-family: inherit;
		text-decoration: none;
	}

	.btn-link:hover {
		color: var(--text);
	}

	/* Resolve form */
	.resolve-form {
		margin-top: 10px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.resolve-input {
		width: 100%;
		box-sizing: border-box;
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--bg);
		color: var(--text);
		font-size: 0.85rem;
		font-family: inherit;
	}

	.resolve-input:focus {
		outline: none;
		border-color: var(--accent);
	}

	.resolve-actions {
		display: flex;
		gap: 6px;
	}

	.btn-resolve {
		padding: 5px 14px;
		border-radius: 4px;
		border: none;
		background: #1fa86a;
		color: #fff;
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-escalate {
		padding: 5px 14px;
		border-radius: 4px;
		border: 1px solid #d4940a;
		background: var(--surface);
		color: #d4940a;
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-dismiss {
		padding: 5px 14px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-cancel-sm {
		padding: 5px 14px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		cursor: pointer;
		font-family: inherit;
		margin-left: auto;
	}

	.empty-state {
		text-align: center;
		padding: 40px;
		color: var(--text-muted);
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
	}

	.empty-state p {
		margin: 0;
		font-size: 0.9rem;
	}
</style>
