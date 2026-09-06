<script lang="ts">
	// Autonomous-exception-agent dashboard: resolution rate, escalation rate,
	// accuracy (placeholder), plus the recent decision log. Read-only surface over
	// GET /api/exceptions/agent-stats + /agent-decisions (admin/ap_manager-gated).
	import { onMount } from 'svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import {
		getAgentStats,
		getAgentDecisions,
		getAgentCandidates,
		runExceptionAgent
	} from '$lib/api/exceptionAgents';
	import {
		ACTION_LABELS,
		type AgentStats,
		type AgentDecision,
		type AgentCandidateException,
		type AgentResolveResult
	} from '$lib/types/exceptionAgents';
	import { appendUnique } from '$lib/utils/pagination';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import { formatDate } from '$lib/utils/time';

	const PAGE_SIZE = 20;

	let stats = $state<AgentStats | null>(null);
	let decisions = $state<AgentDecision[]>([]);
	let total = $state(0);
	let page = $state(1);
	let loading = $state(true);
	let loadingMore = $state(false);
	let actionFilter = $state<string | null>(null);

	// --- The run action -------------------------------------------------------
	// `POST /api/exceptions/{id}/agent-resolve` had no caller anywhere in the
	// app: this dashboard reported on agent activity that could only be
	// triggered outside the product. Reporting on a capability nobody can invoke
	// from here is what made the whole surface read-only theatre.
	let candidates = $state<AgentCandidateException[]>([]);
	let candidatesLoading = $state(true);
	let candidatesErrored = $state(false);
	let runTarget = $state<AgentCandidateException | null>(null);
	let runBusy = $state(false);
	let runError = $state<string | null>(null);
	let runOutcome = $state<AgentResolveResult | null>(null);

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

	// Sequences every `loadDecisions` call — mount, action chip, load-more; one
	// counter, latest-issued wins. Without it two fast chip clicks (or a chip
	// click while a load-more is out) let the earlier response land last and
	// publish the previous filter's rows, `total` and `page`. The KPI stats are
	// a one-shot mount read of DIFFERENT state, so they stay unsequenced. This
	// surface never edits a row in place, so no `supersedeInFlight()` call is
	// needed. See `frontend/CLAUDE.md` § Sequencing list fetches.
	const decisionsSequence = createRequestSequencer();

	onMount(load);

	async function load() {
		loading = true;
		try {
			const [s] = await Promise.all([getAgentStats(), loadDecisions(), loadCandidates()]);
			stats = s;
		} catch {
			toast('Failed to load agent activity', 'error');
		} finally {
			loading = false;
		}
	}

	// The runnable queue is fetched separately from the decision log and keeps
	// its own three states. A failed read must never render as "nothing to run
	// an agent on" — that is a claim about the open exception queue, and the
	// same rule the /exceptions queue itself follows.
	async function loadCandidates() {
		candidatesLoading = true;
		candidatesErrored = false;
		try {
			const data = await getAgentCandidates();
			candidates = data.items;
		} catch {
			candidatesErrored = true;
			candidates = [];
		} finally {
			candidatesLoading = false;
		}
	}

	function openRun(exc: AgentCandidateException) {
		runTarget = exc;
		runError = null;
		runOutcome = null;
	}

	function closeRun() {
		const ran = runOutcome !== null;
		runTarget = null;
		runError = null;
		runOutcome = null;
		// Refresh only after a run actually happened — the decision log, the
		// rates above it and the runnable queue all move together.
		if (ran) void refreshAfterRun();
	}

	async function refreshAfterRun() {
		try {
			stats = await getAgentStats();
		} catch {
			// The rates are a read-only summary; a failed refresh must not
			// overwrite what is on screen with an error state for a run that
			// succeeded. The next mount re-reads them.
		}
		await Promise.all([loadDecisions(), loadCandidates()]);
	}

	async function commitRun() {
		if (!runTarget || runBusy) return;
		runBusy = true;
		runError = null;
		try {
			runOutcome = await runExceptionAgent(runTarget.id);
		} catch (err) {
			// Persistent, not a toast that fades: 409 (already resolved, or lost
			// a race with a concurrent run) and 422 (invoice-less exception) each
			// carry the actionable half of the refusal, and the operator needs it
			// while the dialog is still open.
			runError = err instanceof Error ? err.message : 'The agent run failed.';
		} finally {
			runBusy = false;
		}
	}

	async function loadDecisions(opts: { append?: boolean; nextPage?: number } = {}) {
		const nextPage = opts.nextPage ?? 1;
		const token = decisionsSequence.start();
		if (opts.append) loadingMore = true;
		try {
			const data = await getAgentDecisions({
				actionTaken: actionFilter ?? undefined,
				page: nextPage,
				pageSize: PAGE_SIZE
			});
			// Superseded by a newer load — discard rather than clobber.
			if (!decisionsSequence.canCommit(token)) return;
			decisions = opts.append ? appendUnique(decisions, data.items) : data.items;
			total = data.total;
			page = nextPage;
		} catch {
			// `isCurrentRequest`, not `canCommit`: only the newest request reports.
			if (!decisionsSequence.isCurrentRequest(token)) return;
			if (!opts.append) toast('Failed to load decision log', 'error');
		} finally {
			if (decisionsSequence.isCurrentRequest(token)) loadingMore = false;
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

	/** An exception with no invoice has nothing for an agent to act on — the
	 *  backend 422s it (a Positive Pay `not_on_file` fraud return is the case).
	 *  Shown with a disabled Run and the reason rather than hidden, so the queue
	 *  the panel lists stays the queue the queue page shows. */
	function isRunnable(exc: AgentCandidateException): boolean {
		return exc.invoice_id !== null;
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

	const RUN_COLUMNS = [
		{ label: 'Raised' },
		{ label: 'Exception' },
		{ label: 'Invoice' },
		{ label: 'Vendor' },
		{ label: 'Status' },
		{ label: '' }
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

	<section class="log-section" data-testid="agent-run-panel">
		<header class="log-head">
			<h2>Run an agent</h2>
			<button class="filter-chip" onclick={loadCandidates} disabled={candidatesLoading}>
				{candidatesLoading ? 'Refreshing…' : 'Refresh'}
			</button>
		</header>
		<!-- What a run actually does, said before the button rather than after
		     it. The coordinator applies a fix only when the resolver's confidence
		     clears the org's autonomy threshold; otherwise it hands the exception
		     to a human. Both paths record one append-only decision. -->
		<p class="run-note">
			Running an agent evaluates one exception and, when its confidence clears this
			organization's autonomy threshold, applies the fix through the same audited path
			a person would use. Below that threshold it escalates to a human instead. Either
			way it records one decision in the log below.
		</p>

		<DataTable
			columns={RUN_COLUMNS}
			isEmpty={candidates.length === 0}
			empty={candidatesLoading
				? 'Loading open exceptions…'
				: candidatesErrored
					? 'Could not load the open exceptions. Try Refresh.'
					: 'No open or escalated exceptions to run an agent on.'}
			colspan={6}
		>
			{#snippet body()}
				{#each candidates as exc (exc.id)}
					<tr>
						<td class="muted-cell" title={exc.created_at}>{formatDate(exc.created_at)}</td>
						<td>{exc.type_label}</td>
						<td class="mono">{exc.invoice_number ?? '—'}</td>
						<td class="muted-cell">{exc.vendor_name ?? '—'}</td>
						<td class="muted-cell">{exc.status}</td>
						<td class="right">
							{#if isRunnable(exc)}
								<RowAction
									variant="accent"
									ariaLabel={`Run agent on ${exc.type_label} exception${exc.invoice_number ? ` for invoice ${exc.invoice_number}` : ''}`}
									onclick={() => openRun(exc)}
								>
									Run agent
								</RowAction>
							{:else}
								<!-- Disabled with the reason attached, not omitted: an
								     invoice-less exception is human triage only and the
								     backend 422s it. A missing button explains nothing. -->
								<RowAction
									disabled
									title="This exception has no invoice, so an agent has nothing to act on — human triage only."
									ariaLabel={`Cannot run an agent on this ${exc.type_label} exception: it has no invoice`}
								>
									Run agent
								</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>
	</section>

	<section class="log-section" data-testid="agent-decision-log">
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

<!-- Confirm-then-act: a run can MUTATE the invoice (the resolver applies its fix
     through the same audited path a human would), so it is never a bare click.
     The outcome is then rendered from the RESPONSE — `escalated` and
     `no_action` are outcomes of a successful run, not failures. -->
<Modal
	open={runTarget !== null}
	ariaLabel="Run an exception agent"
	title={runOutcome ? 'Agent decision' : 'Run an agent on this exception'}
	onclose={closeRun}
>
	{#if runTarget}
		<p class="modal-hint">
			<strong>{runTarget.type_label}</strong>
			{#if runTarget.invoice_number}· <span class="mono">{runTarget.invoice_number}</span>{/if}
			{#if runTarget.vendor_name}· {runTarget.vendor_name}{/if}
		</p>

		{#if runOutcome}
			<!-- Every outcome renders the same way. Escalation is what the autonomy
			     threshold is FOR: presenting it as a failure would teach operators
			     that the safe path is the broken one. -->
			<div class="run-outcome" data-testid="agent-run-outcome">
				<span
					class="action-badge"
					data-testid="agent-run-action"
					style="background:{ACTION_COLORS[runOutcome.decision.action_taken] ?? '#888'}1f;color:{ACTION_COLORS[runOutcome.decision.action_taken] ?? '#888'}"
				>
					{ACTION_LABELS[runOutcome.decision.action_taken] ?? runOutcome.decision.action_taken}
				</span>
				<span class="run-outcome-status" data-testid="agent-run-status">
					Exception is now <strong>{runOutcome.exception.status}</strong>
				</span>
			</div>

			{#if runOutcome.decision.action_taken === 'escalated'}
				<p class="run-note" data-testid="agent-run-escalated-note">
					The agent's confidence did not clear this organization's autonomy threshold,
					so it escalated the exception to a human instead of changing anything. That
					is a normal, recorded outcome — the decision is in the log below.
				</p>
			{:else if runOutcome.decision.action_taken === 'no_action'}
				<p class="run-note" data-testid="agent-run-no-action-note">
					The agent found nothing it could safely change on this exception. Nothing was
					modified; the decision is recorded in the log below.
				</p>
			{/if}

			<dl class="run-facts" data-testid="agent-run-facts">
				<div>
					<dt>Resolver</dt>
					<dd class="mono">{runOutcome.decision.agent_type}</dd>
				</div>
				<div>
					<dt>Confidence</dt>
					<dd class="mono">{(runOutcome.decision.confidence * 100).toFixed(0)}%</dd>
				</div>
				<div>
					<dt>Autonomy</dt>
					<dd>{runOutcome.decision.autonomy_level}</dd>
				</div>
			</dl>

			{#if runOutcome.decision.rationale}
				<p class="run-rationale" data-testid="agent-run-rationale">
					{runOutcome.decision.rationale}
				</p>
			{/if}
			{#if runOutcome.decision.changes}
				<p class="run-changes" data-testid="agent-run-changes">
					{changeSummary(runOutcome.decision)}
				</p>
			{/if}

			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={closeRun}>Close</button>
			</div>
		{:else}
			<p class="modal-warn" data-testid="agent-run-warning">
				The agent may change this invoice. It applies a fix only when its confidence
				clears the autonomy threshold; otherwise it escalates to a human. Both outcomes
				are recorded.
			</p>
			{#if runError}
				<p class="state error" role="alert" data-testid="agent-run-error">{runError}</p>
			{/if}
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={closeRun}>Cancel</button>
				<button
					type="button"
					class="btn-primary"
					data-testid="agent-run-confirm"
					disabled={runBusy}
					onclick={commitRun}
				>
					{runBusy ? 'Running…' : 'Run agent'}
				</button>
			</div>
		{/if}
	{/if}
</Modal>

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

	.run-note {
		margin: 0;
		font-size: 0.82rem;
		color: var(--text-muted);
		max-width: 78ch;
	}

	.run-outcome {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
		margin-bottom: 10px;
	}

	.run-outcome-status {
		font-size: 0.85rem;
		color: var(--text-muted);
	}

	.run-facts {
		display: flex;
		gap: 20px;
		flex-wrap: wrap;
		margin: 12px 0 0;
	}

	.run-facts dt {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.run-facts dd {
		margin: 2px 0 0;
		font-size: 0.9rem;
		color: var(--text);
	}

	.run-rationale,
	.run-changes {
		margin: 12px 0 0;
		font-size: 0.85rem;
		color: var(--text);
	}

	.run-changes {
		color: var(--text-muted);
		font-family: var(--font-mono);
	}

	/* Amber-neutral, not danger-red: a run is a normal operation that may change
	   the invoice — the box states a consequence, it does not report an error. */
	.modal-warn {
		font-size: 0.82rem;
		color: var(--text);
		margin: 0 0 14px;
		padding: 10px 12px;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 4px;
	}

	.state {
		color: var(--text-muted);
		padding: 0.75rem 0;
	}

	.state.error {
		color: var(--danger);
	}
</style>
