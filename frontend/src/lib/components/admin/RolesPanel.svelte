<script lang="ts">
	import { adminStore } from '$lib/stores/admin.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import type { Role } from '$lib/types/admin';

	const SYSTEM_COLUMNS = [{ label: 'Name' }, { label: 'Description' }];
	const CUSTOM_COLUMNS = [
		{ label: 'Name' },
		{ label: 'Description' },
		{ class: 'actions-col' }
	];

	let creating = $state(false);
	let newName = $state('');
	let newDescription = $state('');

	let editing = $state<Role | null>(null);
	let editDescription = $state('');
	let confirmDeleteId = $state<string | null>(null);
	let saving = $state(false);

	$effect(() => {
		adminStore.fetchRoles();
	});

	/** Open the Create-role modal. Exposed so the tabbed host's PageHeader
	    action button can trigger it (the button lives outside this panel). */
	export function openCreate() {
		newName = '';
		newDescription = '';
		creating = true;
	}

	async function handleCreate() {
		const name = newName.trim();
		if (!name) return;
		saving = true;
		try {
			await adminStore.createRole({
				name,
				description: newDescription.trim() || undefined,
			});
			toast(`Role "${name}" created`, 'success');
			creating = false;
			newName = '';
			newDescription = '';
		} catch (err) {
			toast(extractError(err), 'error');
		} finally {
			saving = false;
		}
	}

	function openEdit(role: Role) {
		editing = role;
		editDescription = role.description ?? '';
	}

	async function handleEdit() {
		if (!editing) return;
		saving = true;
		try {
			await adminStore.updateRole(editing.id, {
				description: editDescription.trim() || undefined,
			});
			toast('Role updated', 'success');
			editing = null;
		} catch (err) {
			toast(extractError(err), 'error');
		} finally {
			saving = false;
		}
	}

	async function handleDelete(id: string) {
		try {
			await adminStore.deleteRole(id);
			toast('Role deleted', 'success');
		} catch (err) {
			toast(extractError(err), 'error');
		} finally {
			confirmDeleteId = null;
		}
	}

	function handleWindowClick(e: MouseEvent) {
		if (
			confirmDeleteId &&
			!(e.target as HTMLElement).closest('.row-action')
		) {
			confirmDeleteId = null;
		}
	}

	function extractError(err: unknown): string {
		const e = err as { detail?: string; message?: string } | null;
		return e?.detail ?? e?.message ?? 'Request failed';
	}

	let systemRoles = $derived(adminStore.roles.filter((r) => r.is_system));
	let customRoles = $derived(adminStore.roles.filter((r) => !r.is_system));
</script>

<svelte:window onclick={handleWindowClick} />

<section class="role-section">
	<div class="section-header">
		<h2>System roles</h2>
		<p class="section-hint">
			Built-in roles gate hardcoded routes and cannot be edited or deleted.
		</p>
	</div>
	<DataTable columns={SYSTEM_COLUMNS} isEmpty={systemRoles.length === 0} empty="No system roles configured.">
		{#snippet body()}
			{#each systemRoles as role (role.id)}
				<tr>
					<td>
						<span class="role-badge system">{role.name}</span>
					</td>
					<td class="muted-cell">{role.description ?? '—'}</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>
</section>

<section class="role-section">
	<div class="section-header">
		<h2>Custom roles</h2>
		<p class="section-hint">
			Organizational labels you can assign to users for grouping. <strong>Custom roles do
			not grant access</strong> — page and API permissions are controlled only by the four
			system roles above. A user holding only custom roles can sign in but cannot reach any
			restricted screen or action.
		</p>
	</div>
	<DataTable columns={CUSTOM_COLUMNS} isEmpty={customRoles.length === 0} empty="No custom roles yet.">
		{#snippet body()}
			{#each customRoles as role (role.id)}
				<tr>
					<td>
						<span class="role-badge">{role.name}</span>
					</td>
					<td class="muted-cell">{role.description ?? '—'}</td>
					<td class="actions">
						<RowAction onclick={() => openEdit(role)}>Edit</RowAction>
						<RowAction
							variant="danger"
							armed={confirmDeleteId === role.id}
							onclick={(e) => {
								e.stopPropagation();
								if (confirmDeleteId === role.id) {
									handleDelete(role.id);
								} else {
									confirmDeleteId = role.id;
								}
							}}
						>
							{confirmDeleteId === role.id ? 'Confirm' : 'Delete'}
						</RowAction>
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>
</section>

<Modal
	open={creating}
	ariaLabel="Create role"
	width="sm"
	onclose={() => (creating = false)}
>
	<h2>Create role</h2>
	<p class="modal-hint">
		A custom role is an organizational label only — it does not grant any permissions.
		Access is controlled by the system roles (Admin / AP Manager / AP Clerk / CFO).
	</p>
	<form onsubmit={(e) => { e.preventDefault(); handleCreate(); }}>
		<label>
			<span>Name</span>
			<input type="text" bind:value={newName} required maxlength="50" placeholder="e.g. Approver" />
		</label>
		<label>
			<span>Description</span>
			<input type="text" bind:value={newDescription} maxlength="255" placeholder="Optional" />
		</label>
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (creating = false)}>Cancel</button>
			<button type="submit" class="btn-primary" disabled={!newName.trim() || saving}>
				{saving ? 'Creating…' : 'Create'}
			</button>
		</div>
	</form>
</Modal>

<Modal
	open={editing !== null}
	ariaLabel="Edit role"
	width="sm"
	onclose={() => (editing = null)}
>
	{#if editing}
		<h2>Edit "{editing.name}"</h2>
		<p class="modal-hint">
			Role names are immutable once created — edit the description only. Remember a custom
			role grants no access; permissions come from the system roles.
		</p>
		<form onsubmit={(e) => { e.preventDefault(); handleEdit(); }}>
			<label>
				<span>Description</span>
				<input type="text" bind:value={editDescription} maxlength="255" />
			</label>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (editing = null)}>Cancel</button>
				<button type="submit" class="btn-primary" disabled={saving}>
					{saving ? 'Saving…' : 'Save'}
				</button>
			</div>
		</form>
	{/if}
</Modal>

<style>
	/* Panel-specific styling; shared design-system CSS lives in app.css. */
	.role-section {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.section-header h2 {
		margin: 0 0 2px;
		font-size: 0.95rem;
		font-weight: 600;
		color: var(--text);
	}

	.section-hint {
		margin: 0;
		color: var(--text-muted);
		font-size: 0.82rem;
	}

	.muted-cell {
		color: var(--text-muted);
	}

	.role-badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		background: rgba(99, 140, 255, 0.1);
		color: var(--accent);
		font-size: 0.78rem;
		font-weight: 500;
		white-space: nowrap;
	}

	.role-badge.system {
		background: rgba(138, 143, 160, 0.1);
		color: var(--text-muted);
	}
</style>
