<script lang="ts">
	// Autonomous-exception-agent dashboard: resolution rate, escalation rate,
	// accuracy (placeholder), plus the recent decision log. Read-only surface over
	// GET /api/exceptions/agent-stats + /agent-decisions (admin/ap_manager-gated).
	import { onMount } from 'svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { getAgentStats, getAgentDecisions } from '$lib/api/exceptionAgents';
	import { ACTION_LABELS, type AgentStats, type AgentDecision } from '$lib/types/exceptionAgents';

	const PAGE_SIZE = 20;

	let stats = $state<AgentStats | null>(null);
	let decisions = $state<AgentDecision[]>([]);
	let total = $state(0);
	let page = $state(1);
	let loading = $state(true);
	let loadingMore = $state(false);
	let actionFilter = $state<string | null>(null);

	let hasMore = $derived(decisions.length < total);

	// Action-badge tones reuse the exceptions palette (WCAG-passing siblings).
	const ACTION_COLORS: Record<string, string> = {
		auto_resolved: '#1fa86a',
		escalated: '#f06464',
		no_action: '#8a8fa0'
	};

	function pct(rate: number): string {
		return `${(rate * 100).toFixed(1)}%`;
	}

	onMount(load);

	async function load() {
		loading = true;
		try {
			const [s] = await Promise.all([getAgentStats(), loadDecisions()]);
			stats = s;
		} catch {
			toast('Failed to load agent activity', 'error');
		} finally {
			loading = false;
		}
	}

	async function loadDecisions(opts: { append?: boolean; nextPage?: number } = {}) {
		const nextPage = opts.nextPage ?? 1;
		if (opts.append) loadingMore = true;
		try {
			const data = await getAgentDecisions({
				actionTaken: actionFilter ?? undefined,
				page: nextPage,
				pageSize: PAGE_SIZE
			});
			decisions = opts.append ? [...decisions, ...data.items] : data.items;
			total = data.total;
			page = nextPage;
		} catch {
			if (!opts.append) toast('Failed to load decision log', 'error');
		} finally {
			loadingMore = false;
		}
	}

	async function loadMore() {
		await loadDecisions({ append: true, nextPage: page + 1 });
	}

	function setActionFilter(action: string | null) {
		if (actionFilter === action) return;
		actionFilter = action;
		loadDecisions();
	}

	function formatDate(iso: string): string {
		return new Date(iso).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			hour: 'numeric',
			minute: '2-digit'
		});
	}

	function changeSummary(d: AgentDecision): string {
		if (!d.changes) return '—';
		return Object.entries(d.changes)
			.map(([field, v]) => `${field}: ${v.old || '∅'} → ${v.new}`)
			.join(', ');
	}

	const COLUMNS = [
		{ label: 'When' },
		{ label: 'Resolver' },
		{ label: 'Exception' },
		{ label: 'Action' },
		{ label: 'Confidence', class: 'right' },
		{ label: 'Autonomy' },
		{ label: 'Change' }
	];

	const ACTION_CHIPS = [
		{ key: null, label: 'All' },
		{ key: 'auto_resolved', label: 'Auto-resolved' },
		{ key: 'escalated', label: 'Escalated' },
		{ key: 'no_action', label: 'No action' }
	];
</script>

<div class="agent-dash" data-testid="agent-dashboard">
	{#if stats}
		<div class="kpi-row">
			<KpiCard value={stats.total_decisions} label="Decisions made" />
			<KpiCard value={pct(stats.resolution_rate)} label="Resolution rate" highlight="green" />
			<KpiCard value={pct(stats.escalation_rate)} label="Escalation rate" />
			<KpiCard value={stats.auto_resolved} label="Auto-resolved" />
			<KpiCard value={stats.escalated} label="Escalated" />
		</div>

		<!-- Accuracy is a placeholder pending a human-overturn signal — never
		     fabricate a number; show the explicit deferred state. -->
		<div class="accuracy-card" data-testid="agent-accuracy">
			<div class="accuracy-head">
				<span class="accuracy-label">Accuracy</span>
				<span class="accuracy-value">
					{stats.accuracy === null ? 'Not yet measured' : pct(stats.accuracy)}
				</span>
			</div>
			{#if stats.accuracy === null}
				<p class="accuracy-note">
					Accuracy needs a human-overturn signal (was an auto-resolution later
					reversed?). That signal is not tracked yet, so no accuracy figure is
					shown rather than a fabricated one.
				</p>
			{/if}
		</div>
	{:else if loading}
		<p class="dash-loading">Loading agent activity…</p>
	{/if}

	<section class="log-section">
		<header class="log-head">
			<h2>Recent decisions</h2>
			<nav class="filters" aria-label="Filter agent decisions by action">
				{#each ACTION_CHIPS as chip (chip.key ?? 'all')}
					<button
						class="filter-chip"
						class:active={actionFilter === chip.key}
						aria-pressed={actionFilter === chip.key}
						onclick={() => setActionFilter(chip.key)}
					>
						{chip.label}
					</button>
				{/each}
			</nav>
		</header>

		<DataTable
			columns={COLUMNS}
			isEmpty={decisions.length === 0 && !loading}
			empty="No agent decisions yet. Run an agent on an exception to populate this log."
			colspan={7}
		>
			{#snippet body()}
				{#each decisions as d (d.id)}
					<tr>
						<td class="muted-cell" title={d.created_at}>{formatDate(d.created_at)}</td>
						<td class="mono">{d.agent_type}</td>
						<td class="muted-cell">{d.exception_type.replace(/_/g, ' ')}</td>
						<td>
							<span
								class="action-badge"
								style="background:{ACTION_COLORS[d.action_taken] ?? '#888'}1f;color:{ACTION_COLORS[d.action_taken] ?? '#888'}"
							>
								{ACTION_LABELS[d.action_taken] ?? d.action_taken}
							</span>
						</td>
						<td class="mono right">{(d.confidence * 100).toFixed(0)}%</td>
						<td class="muted-cell">{d.autonomy_level}</td>
						<td class="muted-cell change-cell" title={d.rationale ?? ''}>{changeSummary(d)}</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>

		{#if hasMore}
			<div class="load-more-row">
				<button class="btn-load-more" onclick={loadMore} disabled={loadingMore}>
					{loadingMore ? 'Loading…' : `Load more (${decisions.length} of ${total})`}
				</button>
			</div>
		{:else if total > 0}
			<div class="load-more-row">
				<span class="load-more-end">Showing all {total} decision{total === 1 ? '' : 's'}</span>
			</div>
		{/if}
	</section>
</div>

<style>
	.agent-dash {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.dash-loading {
		color: var(--text-muted);
		font-size: 0.9rem;
	}

	.accuracy-card {
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 14px 16px;
		background: var(--surface);
	}

	.accuracy-head {
		display: flex;
		align-items: baseline;
		gap: 12px;
	}

	.accuracy-label {
		font-size: 0.8rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.accuracy-value {
		font-size: 1.1rem;
		font-weight: 600;
		color: var(--text);
	}

	.accuracy-note {
		margin: 8px 0 0;
		font-size: 0.8rem;
		color: var(--text-muted);
		max-width: 60ch;
	}

	.log-section {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.log-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 8px;
	}

	.log-head h2 {
		margin: 0;
		font-size: 1rem;
	}

	.action-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.72rem;
		font-weight: 600;
		white-space: nowrap;
	}

	.muted-cell {
		color: var(--text-muted);
	}

	.change-cell {
		max-width: 280px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
</style>
