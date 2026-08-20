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
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import { formatDate } from '$lib/utils/time';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { pruneSelection } from '$lib/utils/selection';
	import { goto } from '$app/navigation';
	import TemplateLibraryModal from '$lib/components/workflow-mgmt/TemplateLibraryModal.svelte';
	import VersionHistoryModal from '$lib/components/workflow-mgmt/VersionHistoryModal.svelte';
	import SimulationModal from '$lib/components/workflow-mgmt/SimulationModal.svelte';
	import ImportExportControls, {
		exportWorkflowToFile,
	} from '$lib/components/workflow-mgmt/ImportExportControls.svelte';

	let showCreate = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let creating = $state(false);

	// No-code builder management modals (Worker D).
	let showTemplates = $state(false);
	let showImport = $state(false);
	let versionsFor = $state<WorkflowDefinition | null>(null);
	let simulateFor = $state<WorkflowDefinition | null>(null);

	let selectedIds = $state<Set<string>>(new Set());
	let bulkDeleting = $state(false);

	$effect(() => {
		// Fire-and-forget: the store loaders re-throw so an awaiting caller keeps
		// its own handling, but nothing awaits here — the store's `errored` flag is
		// what the UI renders. Swallow so a failed load isn't an unhandled rejection.
		workflowStore.fetch().catch(() => {});
	});

	let selectableIds = $derived(
		workflowStore.all.filter((w) => !w.is_default).map((w) => w.id)
	);

	// Keep the selection ⊆ the rows Bulk delete can actually act on. The store
	// replaces the list on `fetch()` (mount, entity switch) and edits it in
	// place on create / update / restore / delete, so a selected definition can
	// disappear — or be promoted to default — while its id stays in
	// `selectedIds`: the bulk bar then counts rows that are no longer on screen
	// and `bulkRemove` POSTs them, coming back with a `not_found` / `default`
	// failure the user can't see the cause of. `pruneSelection` returns the SAME
	// Set when nothing went stale, so this guarded reassignment can't loop.
	// Same guard as /invoices, /exceptions, /expenses and /payments.
	$effect(() => {
		const pruned = pruneSelection(selectedIds, selectableIds);
		if (pruned !== selectedIds) selectedIds = pruned;
	});

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
				toast(m('workflows.list.bulkDeleted', { n: result.deleted.length }), 'success');
			} else if (result.deleted.length === 0) {
				toast(
					m('workflows.list.bulkNoneDeleted', { reason: describeBulkFailure(result.failed[0]) }),
					'error',
				);
			} else {
				toast(
					m('workflows.list.bulkPartial', {
						deleted: result.deleted.length,
						blocked: result.failed.length,
					}),
					'success',
				);
			}
		} catch (e) {
			toast(e instanceof Error ? e.message : m('workflows.list.bulkDeleteFailed'), 'error');
		} finally {
			bulkDeleting = false;
		}
	}

	function describeBulkFailure(f: { reason: string; instance_count: number | null }): string {
		switch (f.reason) {
			case 'default':
				return m('workflows.list.fail.default');
			case 'active':
				return m('workflows.list.fail.active');
			case 'instances':
				return m('workflows.list.fail.instances', { n: f.instance_count ?? 0 });
			case 'not_found':
				return m('workflows.list.fail.notFound');
			default:
				return m('workflows.list.fail.blocked');
		}
	}

	function stepSummary(wf: WorkflowDefinition): string {
		const steps = wf.steps_config?.steps ?? [];
		return steps
			.filter((s: { enabled: boolean }) => s.enabled)
			.map((s: { type: WorkflowStepType }) => STEP_TYPE_LABELS[s.type] ?? s.type)
			.join(' → ');
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
						name: m('workflows.list.step.extraction'),
						enabled: true,
						config: { ...DEFAULT_EXTRACTION_CONFIG },
					},
					{
						number: 2,
						type: 'approval',
						name: m('workflows.list.step.approval'),
						enabled: true,
						config: { ...DEFAULT_APPROVAL_CONFIG },
					},
					{
						number: 3,
						type: 'erp_export',
						name: m('workflows.list.step.erpExport'),
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
			toast(e instanceof Error ? e.message : m('workflows.list.createFailed'), 'error');
		} finally {
			creating = false;
		}
	}

	async function handleDelete(wf: WorkflowDefinition) {
		if (wf.is_default) return;
		try {
			await workflowStore.remove(wf.id);
			toast(m('workflows.list.deleted'), 'success');
		} catch (e: unknown) {
			toast(e instanceof Error ? e.message : m('workflows.list.deleteFailed'), 'error');
		}
	}
</script>

<PageHeader title={m('workflows.list.title')}>
	{#snippet actions()}
		<button class="btn-toolbar" onclick={() => (showTemplates = true)}>{m('workflows.list.newFromTemplate')}</button>
		<button class="btn-toolbar" onclick={() => (showImport = true)}>{m('workflows.list.import')}</button>
		<button class="btn-create" onclick={() => (showCreate = true)}>{m('workflows.list.newWorkflow')}</button>
	{/snippet}

	<BulkBar count={selectedIds.size} onclear={() => (selectedIds = new Set())}>
		{#snippet actions()}
			<BulkDeleteButton
				onconfirm={handleBulkDelete}
				disabled={bulkDeleting}
				label={m('workflows.list.deleteN', { n: selectedIds.size })}
			/>
		{/snippet}
	</BulkBar>

	<!-- Three states, not two. The mount effect swallows the load rejection (the
	     store's `errored` flag is what the UI renders — a flag the store did NOT
	     have until now), so a 500 left this page asserting "No workflows
	     configured." with no toast and no retry, on the screen that decides
	     whether invoices get routed for approval at all. -->
	<DataTable
		isEmpty={workflowStore.all.length === 0}
		empty={workflowStore.loading
			? m('common.loading')
			: workflowStore.errored
				? m('common.loadFailed')
				: m('workflows.list.empty')}
		colspan={6}
	>
		{#snippet header()}
			<tr>
				<th class="checkbox-col">
					<input
						type="checkbox"
						checked={allSelected}
						onchange={toggleSelectAll}
						aria-label={m('workflows.list.selectAll')}
					/>
				</th>
				<th>{m('workflows.list.col.name')}</th>
				<th>{m('workflows.list.col.steps')}</th>
				<th>{m('workflows.list.col.status')}</th>
				<th>{m('workflows.list.col.created')}</th>
				<th></th>
			</tr>
		{/snippet}
		{#snippet body()}
			{#each workflowStore.all as wf (wf.id)}
				<tr
					class="clickable"
					class:row-selected={selectedIds.has(wf.id)}
					onclick={(e) => {
						if (isRowOpenClick(e)) goto(`/workflows/${wf.id}`);
					}}
				>
					<td class="checkbox-col">
						{#if !wf.is_default}
							<input
								type="checkbox"
								checked={selectedIds.has(wf.id)}
								onchange={() => toggleSelect(wf.id)}
								aria-label={m('workflows.list.selectRow', { name: wf.name })}
							/>
						{/if}
					</td>
					<td>
						<RowLink href="/workflows/{wf.id}" ariaLabel={m('workflows.list.editAria', { name: wf.name })}>
							{wf.name}
							{#if wf.is_default}
								<span class="default-badge">{m('workflows.list.defaultBadge')}</span>
							{/if}
						</RowLink>
						{#if wf.description}
							<div class="wf-desc">{wf.description}</div>
						{/if}
					</td>
					<td class="steps-cell">{stepSummary(wf)}</td>
					<td>
						<span class="status-dot" class:active={wf.is_active} class:inactive={!wf.is_active}>
							{wf.is_active ? m('workflows.list.active') : m('workflows.list.inactive')}
						</span>
					</td>
					<td class="date-cell">{formatDate(wf.created_at)}</td>
					<td class="actions">
						<RowAction onclick={() => (versionsFor = wf)}>{m('workflows.list.action.versions')}</RowAction>
						<RowAction onclick={() => (simulateFor = wf)}>{m('workflows.list.action.simulate')}</RowAction>
						<RowAction onclick={() => exportWorkflowToFile(wf.id, wf.name)}>{m('workflows.list.action.export')}</RowAction>
						{#if !wf.is_default}
							<RowAction variant="danger" onclick={() => handleDelete(wf)}>{m('workflows.list.action.delete')}</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if workflowStore.hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={() => workflowStore.loadMore()} disabled={workflowStore.loading}>
				{workflowStore.loading
					? m('common.loading')
					: m('workflows.list.loadMore', {
							shown: workflowStore.all.length,
							total: workflowStore.total,
						})}
			</button>
		</div>
	{:else if workflowStore.total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('workflows.list.showingAll', { n: workflowStore.total })}</span>
		</div>
	{/if}
</PageHeader>

<Modal
	open={showCreate}
	ariaLabel={m('workflows.list.modal.aria')}
	width="sm"
	onclose={() => (showCreate = false)}
>
	{#snippet header()}
		<h3>{m('workflows.list.modal.title')}</h3>
	{/snippet}
	<div class="form-group">
		<label for="wf-name">{m('workflows.list.modal.name')}</label>
		<input id="wf-name" type="text" bind:value={newName} placeholder={m('workflows.list.modal.namePlaceholder')} />
	</div>
	<div class="form-group">
		<label for="wf-desc">{m('workflows.list.modal.description')}</label>
		<textarea id="wf-desc" bind:value={newDescription} rows="3" placeholder={m('workflows.list.modal.descriptionPlaceholder')}></textarea>
	</div>
	<div class="modal-footer">
		<button class="btn-cancel" onclick={() => (showCreate = false)}>{m('common.cancel')}</button>
		<button class="btn-save" disabled={creating || !newName.trim()} onclick={handleCreate}>
			{creating ? m('workflows.list.modal.creating') : m('workflows.list.modal.create')}
		</button>
	</div>
</Modal>

<TemplateLibraryModal open={showTemplates} onclose={() => (showTemplates = false)} />

<ImportExportControls open={showImport} onclose={() => (showImport = false)} />

{#if versionsFor}
	<VersionHistoryModal
		open={true}
		workflowId={versionsFor.id}
		workflowName={versionsFor.name}
		onclose={() => (versionsFor = null)}
		onrestored={() => workflowStore.fetch()}
	/>
{/if}

{#if simulateFor}
	<SimulationModal
		open={true}
		workflowId={simulateFor.id}
		workflowName={simulateFor.name}
		onclose={() => (simulateFor = null)}
	/>
{/if}

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */
	.btn-create {
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
	}

	.btn-create:hover {
		opacity: 0.85;
	}

	.btn-toolbar {
		padding: 8px 16px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		transition: border-color 0.15s;
	}

	.btn-toolbar:hover {
		border-color: var(--accent);
		color: var(--accent);
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

	.default-badge {
		font-size: 0.68rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		padding: 2px 7px;
		border-radius: 4px;
		background: var(--accent-tint);
		color: var(--accent-on-tint);
		margin-left: 8px;
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
		background: var(--accent-strong);
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
