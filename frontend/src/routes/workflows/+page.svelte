<script lang="ts">
	import type { WorkflowDefinition } from '$lib/types/workflow';
	import { STEP_TYPE_LABELS } from '$lib/types/workflow';
	import type { WorkflowStepType } from '$lib/types/workflow';
	import { workflowStore } from '$lib/stores/workflows.svelte';
	import {
		DEFAULT_EXTRACTION_CONFIG,
		DEFAULT_APPROVAL_CONFIG,
		DEFAULT_ERP_CONFIG,
	} from '$lib/types/workflow';
	import BulkBar from '$lib/components/ui/BulkBar.svelte';
	import BulkDeleteButton from '$lib/components/ui/BulkDeleteButton.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';

	let showCreate = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let creating = $state(false);

	let selectedIds = $state<Set<string>>(new Set());
	let bulkDeleting = $state(false);

	$effect(() => {
		workflowStore.fetch();
	});

	let selectableIds = $derived(
		workflowStore.all.filter((w) => !w.is_default).map((w) => w.id)
	);
	let allSelected = $derived(
		selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id))
	);

	function toggleSelect(id: string) {
		const next = new Set(selectedIds);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		selectedIds = next;
	}

	function toggleSelectAll() {
		selectedIds = allSelected ? new Set() : new Set(selectableIds);
	}

	async function handleBulkDelete() {
		if (selectedIds.size === 0) return;
		bulkDeleting = true;
		try {
			const result = await workflowStore.bulkRemove([...selectedIds]);
			selectedIds = new Set();
			if (result.failed.length === 0) {
				toast(`Deleted ${result.deleted.length} workflow${result.deleted.length === 1 ? '' : 's'}`, 'success');
			} else if (result.deleted.length === 0) {
				toast(`No workflows deleted — ${describeBulkFailure(result.failed[0])}`, 'error');
			} else {
				toast(
					`Deleted ${result.deleted.length}; ${result.failed.length} blocked`,
					'success',
				);
			}
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Bulk delete failed', 'error');
		} finally {
			bulkDeleting = false;
		}
	}

	function describeBulkFailure(f: { reason: string; instance_count: number | null }): string {
		switch (f.reason) {
			case 'default':
				return 'cannot delete the default workflow';
			case 'active':
				return 'workflow is active — deactivate it first';
			case 'instances':
				return `workflow has ${f.instance_count} in-flight invoice${f.instance_count === 1 ? '' : 's'}`;
			case 'not_found':
				return 'workflow not found';
			default:
				return 'blocked';
		}
	}

	function stepSummary(wf: WorkflowDefinition): string {
		const steps = wf.steps_config?.steps ?? [];
		return steps
			.filter((s: { enabled: boolean }) => s.enabled)
			.map((s: { type: WorkflowStepType }) => STEP_TYPE_LABELS[s.type] ?? s.type)
			.join(' → ');
	}

	function formatDate(iso: string): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric',
		});
	}

	async function handleCreate() {
		if (!newName.trim()) return;
		creating = true;
		try {
			const created = await workflowStore.create({
				name: newName.trim(),
				description: newDescription.trim() || undefined,
				steps: [
					{
						number: 1,
						type: 'extraction',
						name: 'Data Extraction',
						enabled: true,
						config: { ...DEFAULT_EXTRACTION_CONFIG },
					},
					{
						number: 2,
						type: 'approval',
						name: 'Approval',
						enabled: true,
						config: { ...DEFAULT_APPROVAL_CONFIG },
					},
					{
						number: 3,
						type: 'erp_export',
						name: 'ERP Export',
						enabled: true,
						config: { ...DEFAULT_ERP_CONFIG },
					},
				],
			});
			showCreate = false;
			newName = '';
			newDescription = '';
			window.location.href = `/workflows/${created.id}`;
		} catch (e: unknown) {
			toast(e instanceof Error ? e.message : 'Failed to create workflow', 'error');
		} finally {
			creating = false;
		}
	}

	async function handleDelete(wf: WorkflowDefinition) {
		if (wf.is_default) return;
		try {
			await workflowStore.remove(wf.id);
			toast('Workflow deleted', 'success');
		} catch (e: unknown) {
			toast(e instanceof Error ? e.message : 'Failed to delete workflow', 'error');
		}
	}
</script>

<PageHeader title="Workflows">
	{#snippet actions()}
		<button class="btn-create" onclick={() => (showCreate = true)}>+ New Workflow</button>
	{/snippet}

	<BulkBar count={selectedIds.size} onclear={() => (selectedIds = new Set())}>
		{#snippet actions()}
			<BulkDeleteButton
				onconfirm={handleBulkDelete}
				disabled={bulkDeleting}
				label={`Delete ${selectedIds.size}`}
			/>
		{/snippet}
	</BulkBar>

	<DataTable
		isEmpty={workflowStore.all.length === 0}
		empty={workflowStore.loading ? 'Loading...' : 'No workflows configured.'}
		colspan={6}
	>
		{#snippet header()}
			<tr>
				<th class="checkbox-col">
					<input
						type="checkbox"
						checked={allSelected}
						onchange={toggleSelectAll}
						aria-label="Select all workflows"
					/>
				</th>
				<th>Name</th>
				<th>Steps</th>
				<th>Status</th>
				<th>Created</th>
				<th></th>
			</tr>
		{/snippet}
		{#snippet body()}
			{#each workflowStore.all as wf (wf.id)}
				<tr class:row-selected={selectedIds.has(wf.id)}>
					<td class="checkbox-col">
						{#if !wf.is_default}
							<input
								type="checkbox"
								checked={selectedIds.has(wf.id)}
								onchange={() => toggleSelect(wf.id)}
								aria-label="Select {wf.name}"
							/>
						{/if}
					</td>
					<td>
						<a href="/workflows/{wf.id}" class="wf-name">
							{wf.name}
							{#if wf.is_default}
								<span class="default-badge">Default</span>
							{/if}
						</a>
						{#if wf.description}
							<div class="wf-desc">{wf.description}</div>
						{/if}
					</td>
					<td class="steps-cell">{stepSummary(wf)}</td>
					<td>
						<span class="status-dot" class:active={wf.is_active} class:inactive={!wf.is_active}>
							{wf.is_active ? 'Active' : 'Inactive'}
						</span>
					</td>
					<td class="date-cell">{formatDate(wf.created_at)}</td>
					<td class="actions">
						<RowAction href="/workflows/{wf.id}">Edit</RowAction>
						{#if !wf.is_default}
							<RowAction variant="danger" onclick={() => handleDelete(wf)}>Delete</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>
</PageHeader>

<Modal
	open={showCreate}
	ariaLabel="Create workflow"
	width="sm"
	onclose={() => (showCreate = false)}
>
	{#snippet header()}
		<h3>Create Workflow</h3>
	{/snippet}
	<div class="form-group">
		<label for="wf-name">Name</label>
		<input id="wf-name" type="text" bind:value={newName} placeholder="e.g. High-Value Invoice Review" />
	</div>
	<div class="form-group">
		<label for="wf-desc">Description</label>
		<textarea id="wf-desc" bind:value={newDescription} rows="3" placeholder="Optional description..."></textarea>
	</div>
	<div class="modal-footer">
		<button class="btn-cancel" onclick={() => (showCreate = false)}>Cancel</button>
		<button class="btn-save" disabled={creating || !newName.trim()} onclick={handleCreate}>
			{creating ? 'Creating...' : 'Create'}
		</button>
	</div>
</Modal>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */
	.btn-create {
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
	}

	.btn-create:hover {
		opacity: 0.85;
	}

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

	.wf-name {
		font-weight: 500;
		color: var(--accent);
		text-decoration: none;
		display: inline-flex;
		align-items: center;
		gap: 8px;
	}

	.wf-name:hover {
		text-decoration: underline;
	}

	.default-badge {
		font-size: 0.68rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		padding: 2px 7px;
		border-radius: 4px;
		background: rgba(99, 140, 255, 0.15);
		color: var(--accent);
	}

	.wf-desc {
		margin-top: 2px;
		font-size: 0.8rem;
		color: var(--text-muted);
	}

	.steps-cell {
		font-size: 0.82rem;
		color: var(--text-muted);
		white-space: nowrap;
	}

	.date-cell {
		white-space: nowrap;
		color: var(--text-muted);
		font-size: 0.82rem;
	}

	.status-dot {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 0.82rem;
		font-weight: 500;
	}

	.status-dot::before {
		content: '';
		width: 7px;
		height: 7px;
		border-radius: 50%;
	}

	.status-dot.active::before {
		background: #1fa86a;
	}

	.status-dot.inactive::before {
		background: #999;
	}

	.status-dot.active {
		color: #1fa86a;
	}

	.status-dot.inactive {
		color: #999;
	}

	/* Create-workflow modal: custom h3 heading + labelled form fields. */
	.modal h3 {
		margin: 0 0 18px;
		font-size: 1.05rem;
		color: var(--text);
	}

	.form-group {
		margin-bottom: 14px;
	}

	.form-group label {
		display: block;
		font-size: 0.78rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		margin-bottom: 5px;
	}

	.form-group input,
	.form-group textarea {
		width: 100%;
		padding: 8px 10px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.88rem;
		outline: none;
		box-sizing: border-box;
	}

	.form-group input:focus,
	.form-group textarea:focus {
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.form-group textarea {
		resize: vertical;
	}

	.btn-save {
		padding: 8px 18px;
		border-radius: 6px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-save:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
