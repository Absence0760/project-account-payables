<script lang="ts">
	import type {
		Experiment,
		ExperimentResults,
		PrimaryMetric
	} from '$lib/types/experiments';
	import {
		STATUS_LABELS,
		PRIMARY_METRIC_LABELS
	} from '$lib/types/experiments';
	import type { WorkflowDefinition } from '$lib/types/workflow';
	import { auth } from '$lib/stores/auth.svelte';
	import { api } from '$lib/api';
	import {
		listExperiments,
		createExperiment,
		startExperiment,
		stopExperiment,
		concludeExperiment,
		deleteExperiment,
		getExperimentResults
	} from '$lib/api/experiments';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';

	const canMutate = $derived(auth.isAdmin);

	const METRICS: PrimaryMetric[] = [
		'time_to_approval_days',
		'touchless_rate_pct',
		'exception_rate_pct',
		'rejection_rate_pct'
	];

	const COLUMNS = [
		{ label: 'Name' },
		{ label: 'Workflow' },
		{ label: 'Primary metric' },
		{ label: 'Split (A/B)' },
		{ label: 'Assigned', class: 'right' },
		{ label: 'Status' },
		{ class: 'actions-col' }
	];

	let experiments = $state<Experiment[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let statusFilter = $state<string>('all');

	let definitions = $state<WorkflowDefinition[]>([]);

	// Create modal state.
	let showCreate = $state(false);
	let saving = $state(false);
	let createError = $state<string | null>(null);
	let form = $state({
		name: '',
		description: '',
		workflow_definition_id: '',
		split_a_pct: 50,
		primary_metric: 'time_to_approval_days' as PrimaryMetric,
		min_sample_per_variant: 10,
		config_a: '{\n  "steps": []\n}',
		config_b: '{\n  "steps": []\n}'
	});

	// Results modal state.
	let resultsFor = $state<Experiment | null>(null);
	let results = $state<ExperimentResults | null>(null);
	let resultsLoading = $state(false);
	let resultsError = $state<string | null>(null);

	const filtered = $derived(
		statusFilter === 'all'
			? experiments
			: experiments.filter((e) => e.status === statusFilter)
	);

	const STATUS_CHIPS = $derived([
		{ key: 'all', label: 'All', count: experiments.length },
		{ key: 'draft', label: 'Draft', count: experiments.filter((e) => e.status === 'draft').length },
		{ key: 'running', label: 'Running', count: experiments.filter((e) => e.status === 'running').length },
		{
			key: 'concluded',
			label: 'Concluded',
			count: experiments.filter((e) => e.status === 'concluded').length
		}
	]);

	async function load() {
		loading = true;
		loadError = null;
		try {
			const res = await listExperiments();
			experiments = res.experiments;
		} catch {
			loadError = 'Failed to load experiments.';
		} finally {
			loading = false;
		}
	}

	async function loadDefinitions() {
		try {
			const res = await api.get<{ items: WorkflowDefinition[] }>(
				'/api/workflows?page=1&page_size=100'
			);
			definitions = res.items ?? [];
		} catch {
			definitions = [];
		}
	}

	$effect(() => {
		load();
		if (canMutate) loadDefinitions();
	});

	function openCreate() {
		createError = null;
		form = {
			name: '',
			description: '',
			workflow_definition_id: definitions[0]?.id ?? '',
			split_a_pct: 50,
			primary_metric: 'time_to_approval_days',
			min_sample_per_variant: 10,
			config_a: JSON.stringify(
				definitions[0]?.steps_config ?? { steps: [] },
				null,
				2
			),
			config_b: JSON.stringify(
				definitions[0]?.steps_config ?? { steps: [] },
				null,
				2
			)
		};
		showCreate = true;
	}

	// When the chosen definition changes, seed both configs from its live steps.
	function onDefinitionChange() {
		const defn = definitions.find((d) => d.id === form.workflow_definition_id);
		if (defn) {
			const json = JSON.stringify(defn.steps_config, null, 2);
			form.config_a = json;
			form.config_b = json;
		}
	}

	async function submitCreate() {
		createError = null;
		if (!form.name.trim()) {
			createError = 'Name is required.';
			return;
		}
		if (!form.workflow_definition_id) {
			createError = 'Pick a workflow definition.';
			return;
		}
		let configA: Record<string, unknown>;
		let configB: Record<string, unknown>;
		try {
			configA = JSON.parse(form.config_a);
		} catch {
			createError = 'Variant A config is not valid JSON.';
			return;
		}
		try {
			configB = JSON.parse(form.config_b);
		} catch {
			createError = 'Variant B config is not valid JSON.';
			return;
		}
		saving = true;
		try {
			await createExperiment({
				name: form.name.trim(),
				description: form.description.trim() || null,
				workflow_definition_id: form.workflow_definition_id,
				config_a: configA,
				config_b: configB,
				split_a_pct: form.split_a_pct,
				primary_metric: form.primary_metric,
				min_sample_per_variant: form.min_sample_per_variant
			});
			showCreate = false;
			toast('Experiment created.', 'success');
			await load();
		} catch (e) {
			createError = e instanceof Error ? e.message : 'Failed to create experiment.';
		} finally {
			saving = false;
		}
	}

	async function doStart(exp: Experiment) {
		try {
			await startExperiment(exp.id);
			toast('Experiment started.', 'success');
			await load();
		} catch {
			toast('Failed to start experiment.', 'error');
		}
	}

	async function doStop(exp: Experiment) {
		try {
			await stopExperiment(exp.id);
			toast('Experiment stopped.', 'success');
			await load();
		} catch {
			toast('Failed to stop experiment.', 'error');
		}
	}

	async function doConclude(exp: Experiment) {
		try {
			await concludeExperiment(exp.id);
			toast('Experiment concluded.', 'success');
			await load();
		} catch {
			toast('Failed to conclude experiment.', 'error');
		}
	}

	let confirmDeleteId = $state<string | null>(null);
	async function doDelete(exp: Experiment) {
		if (confirmDeleteId !== exp.id) {
			confirmDeleteId = exp.id;
			return;
		}
		confirmDeleteId = null;
		try {
			await deleteExperiment(exp.id);
			toast('Experiment deleted.', 'success');
			await load();
		} catch {
			toast('Failed to delete experiment.', 'error');
		}
	}

	async function openResults(exp: Experiment) {
		resultsFor = exp;
		results = null;
		resultsError = null;
		resultsLoading = true;
		try {
			results = await getExperimentResults(exp.id);
		} catch {
			resultsError = 'Failed to load results.';
		} finally {
			resultsLoading = false;
		}
	}

	function statusTone(status: string): 'green' | 'amber' | 'grey' {
		if (status === 'running') return 'green';
		if (status === 'draft') return 'amber';
		return 'grey';
	}
</script>

<svelte:window onclick={(e) => {
	if (confirmDeleteId && !(e.target as HTMLElement)?.closest('.row-action')) confirmDeleteId = null;
}} />

<PageHeader title="Workflow Experiments">
	{#snippet actions()}
		{#if canMutate}
			<button class="btn-primary" onclick={openCreate}>+ New experiment</button>
		{/if}
	{/snippet}

	<p class="lede">
		Run a controlled A/B test of two workflow-rule configurations and measure
		which performs better on time-to-approval, touchless rate, exception rate,
		and rejection rate. Routing only — never moves money.
	</p>

	<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />

	{#if loadError}
		<p class="error-banner">{loadError}</p>
	{/if}

	<DataTable
		columns={COLUMNS}
		isEmpty={!loading && filtered.length === 0}
		empty={loading ? 'Loading…' : 'No experiments yet.'}
	>
		{#snippet body()}
			{#each filtered as exp (exp.id)}
				<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) openResults(exp); }}>
					<td>
						<RowLink onclick={() => openResults(exp)} ariaLabel={`View results for ${exp.name}`}>
							{exp.name}
						</RowLink>
					</td>
					<td>{exp.workflow_definition_name ?? '—'}</td>
					<td>{PRIMARY_METRIC_LABELS[exp.primary_metric]}</td>
					<td class="mono">{exp.split_a_pct}% / {100 - exp.split_a_pct}%</td>
					<td class="right mono">{exp.assigned_count}</td>
					<td><span class="exp-badge {statusTone(exp.status)}">{STATUS_LABELS[exp.status]}</span></td>
					<td class="actions">
						{#if canMutate && exp.status === 'draft'}
							<RowAction variant="success" onclick={() => doStart(exp)}>Start</RowAction>
							<RowAction
								variant="danger"
								armed={confirmDeleteId === exp.id}
								onclick={() => doDelete(exp)}
							>
								{confirmDeleteId === exp.id ? 'Confirm' : 'Delete'}
							</RowAction>
						{:else if canMutate && exp.status === 'running'}
							<RowAction onclick={() => doStop(exp)}>Stop</RowAction>
							<RowAction variant="danger" onclick={() => doConclude(exp)}>Conclude</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>
</PageHeader>

<!-- Create experiment -->
<Modal
	open={showCreate}
	ariaLabel="Create experiment"
	title="New experiment"
	width="lg"
	onclose={() => (showCreate = false)}
>
	<form onsubmit={(e) => { e.preventDefault(); submitCreate(); }}>
		<div class="field">
			<label for="exp-name">Name <em class="required">*</em></label>
			<input id="exp-name" type="text" bind:value={form.name} placeholder="Higher auto-approve threshold" />
		</div>
		<div class="field">
			<label for="exp-desc">Description</label>
			<input id="exp-desc" type="text" bind:value={form.description} placeholder="Optional" />
		</div>
		<div class="field">
			<label for="exp-defn">Workflow definition <em class="required">*</em></label>
			<select id="exp-defn" bind:value={form.workflow_definition_id} onchange={onDefinitionChange}>
				{#each definitions as d (d.id)}
					<option value={d.id}>{d.name}</option>
				{/each}
			</select>
		</div>
		<div class="field-row">
			<div class="field">
				<label for="exp-split">Split — % to variant A</label>
				<input id="exp-split" type="number" min="0" max="100" bind:value={form.split_a_pct} />
			</div>
			<div class="field">
				<label for="exp-metric">Primary metric</label>
				<select id="exp-metric" bind:value={form.primary_metric}>
					{#each METRICS as mtr (mtr)}
						<option value={mtr}>{PRIMARY_METRIC_LABELS[mtr]}</option>
					{/each}
				</select>
			</div>
			<div class="field">
				<label for="exp-min">Min sample / variant</label>
				<input id="exp-min" type="number" min="1" bind:value={form.min_sample_per_variant} />
			</div>
		</div>
		<div class="field-row">
			<div class="field">
				<label for="exp-cfg-a">Variant A config (control)</label>
				<textarea id="exp-cfg-a" rows="8" class="mono" bind:value={form.config_a}></textarea>
			</div>
			<div class="field">
				<label for="exp-cfg-b">Variant B config (variant)</label>
				<textarea id="exp-cfg-b" rows="8" class="mono" bind:value={form.config_b}></textarea>
			</div>
		</div>
		{#if createError}
			<p class="error-banner">{createError}</p>
		{/if}
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (showCreate = false)}>Cancel</button>
			<button type="submit" class="btn-primary" disabled={saving}>
				{saving ? 'Saving…' : 'Create'}
			</button>
		</div>
	</form>
</Modal>

<!-- Results readout -->
<Modal
	open={resultsFor !== null}
	ariaLabel="Experiment results"
	title={resultsFor ? `Results — ${resultsFor.name}` : 'Results'}
	width="lg"
	onclose={() => (resultsFor = null)}
>
	{#if resultsLoading}
		<p>Loading results…</p>
	{:else if resultsError}
		<p class="error-banner">{resultsError}</p>
	{:else if results}
		<div class="readout">
			{#if results.enough_data}
				<div class="winner" class:tie={results.winner === 'tie'}>
					{#if results.winner === 'tie'}
						<strong>No clear winner</strong>
					{:else}
						<strong>Winner: Variant {results.winner}</strong>
					{/if}
				</div>
			{:else}
				<div class="winner pending"><strong>Not enough data yet</strong></div>
			{/if}
			<p class="rationale">{results.rationale}</p>
			{#if results.notes.length}
				<ul class="notes">
					{#each results.notes as note (note)}<li>{note}</li>{/each}
				</ul>
			{/if}

			<table class="variant-table">
				<thead>
					<tr>
						<th scope="col">Metric</th>
						<th scope="col" class="right">Variant A</th>
						<th scope="col" class="right">Variant B</th>
					</tr>
				</thead>
				<tbody>
					<tr><td>Assigned</td><td class="right mono">{results.variant_a.assigned_count}</td><td class="right mono">{results.variant_b.assigned_count}</td></tr>
					<tr><td>Completed</td><td class="right mono">{results.variant_a.completed_count}</td><td class="right mono">{results.variant_b.completed_count}</td></tr>
					<tr class:primary={results.primary_metric === 'time_to_approval_days'}>
						<td>Median time to approval (days)</td>
						<td class="right mono">{results.variant_a.median_time_to_approval_days}</td>
						<td class="right mono">{results.variant_b.median_time_to_approval_days}</td>
					</tr>
					<tr class:primary={results.primary_metric === 'touchless_rate_pct'}>
						<td>Touchless rate</td>
						<td class="right mono">{results.variant_a.touchless_rate_pct}%</td>
						<td class="right mono">{results.variant_b.touchless_rate_pct}%</td>
					</tr>
					<tr class:primary={results.primary_metric === 'exception_rate_pct'}>
						<td>Exception rate</td>
						<td class="right mono">{results.variant_a.exception_rate_pct}%</td>
						<td class="right mono">{results.variant_b.exception_rate_pct}%</td>
					</tr>
					<tr class:primary={results.primary_metric === 'rejection_rate_pct'}>
						<td>Rejection rate</td>
						<td class="right mono">{results.variant_a.rejection_rate_pct}%</td>
						<td class="right mono">{results.variant_b.rejection_rate_pct}%</td>
					</tr>
				</tbody>
			</table>
			<p class="hint">
				The highlighted row is the configured primary metric the winner is
				called on. Needs ≥ {results.min_sample_per_variant} completed invoices
				per variant.
			</p>
		</div>
	{/if}
</Modal>

<style>
	.lede {
		color: var(--text-muted);
		max-width: 70ch;
		margin: 0;
	}
	.exp-badge {
		display: inline-block;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		white-space: nowrap;
	}
	.exp-badge.green {
		background: rgba(50, 200, 130, 0.15);
		color: #26b977;
	}
	.exp-badge.amber {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}
	.exp-badge.grey {
		background: rgba(160, 160, 160, 0.15);
		color: #9ca3af;
	}
	.error-banner {
		color: var(--danger, #f87171);
		margin: 8px 0;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 4px;
		margin-bottom: 12px;
		flex: 1;
	}
	.field-row {
		display: flex;
		gap: 12px;
	}
	.field label {
		font-size: 0.85rem;
		color: var(--text-muted);
	}
	textarea.mono {
		font-family: ui-monospace, monospace;
		font-size: 0.8rem;
	}
	.readout .winner {
		padding: 10px 14px;
		border-radius: 8px;
		background: color-mix(in srgb, var(--accent) 18%, transparent);
		margin-bottom: 10px;
	}
	.readout .winner.tie,
	.readout .winner.pending {
		background: color-mix(in srgb, var(--text-muted) 18%, transparent);
	}
	.rationale {
		color: var(--text-muted);
		margin: 0 0 8px;
	}
	.notes {
		color: var(--text-muted);
		font-size: 0.85rem;
		margin: 0 0 12px;
	}
	.variant-table {
		width: 100%;
		border-collapse: collapse;
		margin-top: 8px;
	}
	.variant-table th,
	.variant-table td {
		padding: 6px 10px;
		border-bottom: 1px solid var(--border, #333);
		text-align: left;
	}
	.variant-table .right {
		text-align: right;
	}
	.variant-table tr.primary {
		background: color-mix(in srgb, var(--accent) 12%, transparent);
		font-weight: 600;
	}
	.hint {
		color: var(--text-muted);
		font-size: 0.8rem;
		margin-top: 10px;
	}
</style>
