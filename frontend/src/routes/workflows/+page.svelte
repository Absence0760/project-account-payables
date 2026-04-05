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
	import { toast } from '$lib/components/Toast.svelte';

	let showCreate = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let creating = $state(false);

	$effect(() => {
		workflowStore.fetch();
	});

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

<div class="workspace">
	<header class="toolbar">
		<div class="toolbar-left">
			<h2 class="page-title">Workflows</h2>
		</div>
		<button class="btn-create" onclick={() => (showCreate = true)}>+ New Workflow</button>
	</header>

	<div class="grid-container">
		<table>
			<thead>
				<tr>
					<th>Name</th>
					<th>Steps</th>
					<th>Status</th>
					<th>Created</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each workflowStore.all as wf (wf.id)}
					<tr>
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
						<td>
							<div class="actions">
								<a href="/workflows/{wf.id}" class="edit-btn">Edit</a>
								{#if !wf.is_default}
									<button class="delete-btn" onclick={() => handleDelete(wf)}>Delete</button>
								{/if}
							</div>
						</td>
					</tr>
				{:else}
					<tr>
						<td colspan="5" class="empty">
							{workflowStore.loading ? 'Loading...' : 'No workflows configured.'}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>

{#if showCreate}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="modal-backdrop" onkeydown={(e) => e.key === 'Escape' && (showCreate = false)} onclick={() => (showCreate = false)}>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="modal" onclick={(e) => e.stopPropagation()} onkeydown={() => {}}>
			<h3>Create Workflow</h3>
			<div class="form-group">
				<label for="wf-name">Name</label>
				<input id="wf-name" type="text" bind:value={newName} placeholder="e.g. High-Value Invoice Review" />
			</div>
			<div class="form-group">
				<label for="wf-desc">Description</label>
				<textarea id="wf-desc" bind:value={newDescription} rows="3" placeholder="Optional description..." />
			</div>
			<div class="modal-footer">
				<button class="btn-cancel" onclick={() => (showCreate = false)}>Cancel</button>
				<button class="btn-save" disabled={creating || !newName.trim()} onclick={handleCreate}>
					{creating ? 'Creating...' : 'Create'}
				</button>
			</div>
		</div>
	</div>
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
		justify-content: space-between;
		gap: 16px;
	}

	.toolbar-left {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.page-title {
		font-size: 1.2rem;
		font-weight: 600;
		color: var(--text);
		margin: 0;
	}

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

	.error-bar {
		padding: 10px 14px;
		border-radius: 6px;
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
		font-size: 0.85rem;
	}

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
		padding: 12px 14px;
		border-bottom: 1px solid var(--border);
		color: var(--text);
	}

	tr:last-child td {
		border-bottom: none;
	}

	tbody tr:hover {
		background: rgba(99, 140, 255, 0.04);
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

	.actions {
		display: flex;
		gap: 6px;
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
		text-decoration: none;
	}

	.edit-btn:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.delete-btn {
		padding: 4px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.delete-btn:hover {
		border-color: #e04040;
		color: #e04040;
	}

	.empty {
		text-align: center;
		padding: 40px 14px;
		color: var(--text-muted);
	}

	/* Modal */
	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.6);
		display: grid;
		place-items: center;
		z-index: 100;
	}

	.modal {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 10px;
		padding: 24px;
		width: 90vw;
		max-width: 460px;
	}

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

	.modal-footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 18px;
	}

	.btn-cancel {
		padding: 8px 16px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
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
