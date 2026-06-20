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
		{ label: 'Permissions' },
		{ class: 'actions-col' }
	];

	let creating = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let newPermissions = $state<Set<string>>(new Set());

	let editing = $state<Role | null>(null);
	let editDescription = $state('');
	let editPermissions = $state<Set<string>>(new Set());
	let confirmDeleteId = $state<string | null>(null);
	let saving = $state(false);

	$effect(() => {
		adminStore.fetchRoles();
		adminStore.fetchPermissionCatalog();
	});

	function togglePermission(set: Set<string>, key: string): Set<string> {
		const next = new Set(set);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		return next;
	}

	/** Open the Create-role modal. Exposed so the tabbed host's PageHeader
	    action button can trigger it (the button lives outside this panel). */
	export function openCreate() {
		newName = '';
		newDescription = '';
		newPermissions = new Set();
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
				permissions: [...newPermissions],
			});
			toast(`Role "${name}" created`, 'success');
			creating = false;
			newName = '';
			newDescription = '';
			newPermissions = new Set();
		} catch (err) {
			toast(extractError(err), 'error');
		} finally {
			saving = false;
		}
	}

	function openEdit(role: Role) {
		editing = role;
		editDescription = role.description ?? '';
		editPermissions = new Set(role.permissions ?? []);
	}

	async function handleEdit() {
		if (!editing) return;
		saving = true;
		try {
			await adminStore.updateRole(editing.id, {
				description: editDescription.trim() || undefined,
				permissions: [...editPermissions],
			});
			toast('Role updated', 'success');
			editing = null;
		} catch (err) {
			toast(extractError(err), 'error');
		} finally {
			saving = false;
		}
	}

	function permLabels(role: Role): string[] {
		const labels = adminStore.permissionCatalog;
		return (role.permissions ?? []).map(
			(k) => labels.find((p) => p.key === k)?.label ?? k
		);
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
			Roles you define for your organization. Grant each one specific
			<strong>permissions</strong> below to split fraud-sensitive duties — e.g. a role that
			approves invoices but cannot execute payment runs. A role with no permissions selected
			is an organizational label only and grants no access.
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
					<td>
						{#if permLabels(role).length}
							<div class="perm-chips">
								{#each permLabels(role) as label (label)}
									<span class="perm-chip">{label}</span>
								{/each}
							</div>
						{:else}
							<span class="muted-cell">No permissions</span>
						{/if}
					</td>
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
		Pick the permissions this role grants. Leave them all unchecked to create an
		organizational label that grants no access.
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
		<fieldset class="perm-fieldset">
			<legend>Permissions</legend>
			{#each adminStore.permissionCatalog as perm (perm.key)}
				<label class="perm-option">
					<input
						type="checkbox"
						checked={newPermissions.has(perm.key)}
						onchange={() => (newPermissions = togglePermission(newPermissions, perm.key))}
					/>
					<span>{perm.label}</span>
				</label>
			{/each}
		</fieldset>
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
			Role names are immutable once created — edit the description and permissions only.
		</p>
		<form onsubmit={(e) => { e.preventDefault(); handleEdit(); }}>
			<label>
				<span>Description</span>
				<input type="text" bind:value={editDescription} maxlength="255" />
			</label>
			<fieldset class="perm-fieldset">
				<legend>Permissions</legend>
				{#each adminStore.permissionCatalog as perm (perm.key)}
					<label class="perm-option">
						<input
							type="checkbox"
							checked={editPermissions.has(perm.key)}
							onchange={() => (editPermissions = togglePermission(editPermissions, perm.key))}
						/>
						<span>{perm.label}</span>
					</label>
				{/each}
			</fieldset>
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

	.perm-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}

	.perm-chip {
		display: inline-block;
		padding: 1px 8px;
		border-radius: 8px;
		background: rgba(99, 140, 255, 0.08);
		color: var(--accent);
		font-size: 0.72rem;
		white-space: nowrap;
	}

	.perm-fieldset {
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 10px 12px;
		margin: 4px 0 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.perm-fieldset legend {
		padding: 0 6px;
		font-size: 0.82rem;
		font-weight: 600;
		color: var(--text-muted);
	}

	.perm-option {
		display: flex;
		flex-direction: row;
		align-items: center;
		gap: 8px;
		font-size: 0.85rem;
		cursor: pointer;
	}

	.perm-option input {
		width: auto;
		margin: 0;
	}
</style>
