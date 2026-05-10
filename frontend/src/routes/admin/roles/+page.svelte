<script lang="ts">
	import { adminStore } from '$lib/stores/admin.svelte';
	import { toast } from '$lib/components/Toast.svelte';
	import type { Role } from '$lib/types/admin';

	let creating = $state(false);
	let newName = $state('');
	let newDescription = $state('');

	let editing = $state<Role | null>(null);
	let editDescription = $state('');
	let confirmDeleteId = $state<string | null>(null);

	$effect(() => {
		adminStore.fetchRoles();
	});

	async function handleCreate() {
		const name = newName.trim();
		if (!name) return;
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
		}
	}

	function openEdit(role: Role) {
		editing = role;
		editDescription = role.description ?? '';
	}

	async function handleEdit() {
		if (!editing) return;
		try {
			await adminStore.updateRole(editing.id, { description: editDescription.trim() || undefined });
			toast('Role updated', 'success');
			editing = null;
		} catch (err) {
			toast(extractError(err), 'error');
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

	function extractError(err: unknown): string {
		const e = err as { detail?: string; message?: string } | null;
		return e?.detail ?? e?.message ?? 'Request failed';
	}

	let systemRoles = $derived(adminStore.roles.filter((r) => r.is_system));
	let customRoles = $derived(adminStore.roles.filter((r) => !r.is_system));
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Roles</h1>
		<div class="toolbar-actions">
			<button class="btn-primary" onclick={() => (creating = true)}>+ Create Role</button>
		</div>
	</header>

	<section class="role-section">
		<h2>System roles</h2>
		<p class="section-hint">
			The four built-in roles gate hardcoded routes and cannot be edited or deleted.
		</p>
		<table>
			<thead>
				<tr>
					<th>Name</th>
					<th>Description</th>
				</tr>
			</thead>
			<tbody>
				{#each systemRoles as role (role.id)}
					<tr>
						<td><span class="badge system">{role.name}</span></td>
						<td>{role.description ?? '—'}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>

	<section class="role-section">
		<h2>Custom roles</h2>
		<p class="section-hint">
			Mint additional role names for this organization. Custom roles can be assigned to users
			and referenced in approval-chain configuration.
		</p>
		{#if customRoles.length === 0}
			<p class="empty-state">No custom roles yet.</p>
		{:else}
			<table>
				<thead>
					<tr>
						<th>Name</th>
						<th>Description</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each customRoles as role (role.id)}
						<tr>
							<td><span class="badge">{role.name}</span></td>
							<td>{role.description ?? '—'}</td>
							<td class="row-actions">
								<button class="link-btn" onclick={() => openEdit(role)}>Edit</button>
								{#if confirmDeleteId === role.id}
									<button
										class="link-btn danger armed"
										onclick={() => handleDelete(role.id)}
									>Confirm delete</button>
								{:else}
									<button
										class="link-btn danger"
										onclick={() => (confirmDeleteId = role.id)}
									>Delete</button>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>
</div>

{#if creating}
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) (creating = false); }}>
		<div class="modal" role="dialog" aria-label="Create role">
			<h2>Create role</h2>
			<form onsubmit={(e) => { e.preventDefault(); handleCreate(); }}>
				<label>
					Name<em class="required">*</em>
					<input bind:value={newName} required maxlength="50" autofocus />
				</label>
				<label>
					Description
					<input bind:value={newDescription} maxlength="255" />
				</label>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={() => (creating = false)}>Cancel</button>
					<button type="submit" class="btn-primary" disabled={!newName.trim()}>Create</button>
				</div>
			</form>
		</div>
	</div>
{/if}

{#if editing}
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) (editing = null); }}>
		<div class="modal" role="dialog" aria-label="Edit role">
			<h2>Edit "{editing.name}"</h2>
			<form onsubmit={(e) => { e.preventDefault(); handleEdit(); }}>
				<p class="hint">
					Role names are immutable — they're referenced by approval-chain configs.
					Edit the description only.
				</p>
				<label>
					Description
					<input bind:value={editDescription} maxlength="255" />
				</label>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={() => (editing = null)}>Cancel</button>
					<button type="submit" class="btn-primary">Save</button>
				</div>
			</form>
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
		gap: 24px;
	}

	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
	}

	.toolbar h1 {
		margin: 0;
		font-size: 1.5rem;
	}

	.role-section h2 {
		margin: 0 0 4px;
		font-size: 1.1rem;
	}

	.section-hint {
		margin: 0 0 12px;
		color: var(--text-secondary, #6b7280);
		font-size: 0.875rem;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		background: white;
		border: 1px solid var(--border, #e5e7eb);
		border-radius: 8px;
		overflow: hidden;
	}

	th,
	td {
		text-align: left;
		padding: 10px 14px;
		border-bottom: 1px solid var(--border, #f3f4f6);
		font-size: 0.875rem;
	}

	tr:last-child td {
		border-bottom: none;
	}

	.badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 999px;
		background: var(--accent-soft, #eff6ff);
		color: var(--accent, #2563eb);
		font-weight: 500;
		font-size: 0.8rem;
	}

	.badge.system {
		background: #f3f4f6;
		color: #4b5563;
	}

	.empty-state {
		color: var(--text-secondary, #6b7280);
		font-size: 0.875rem;
		font-style: italic;
	}

	.row-actions {
		display: flex;
		gap: 12px;
		justify-content: flex-end;
	}

	.link-btn {
		background: none;
		border: none;
		color: var(--accent, #2563eb);
		cursor: pointer;
		padding: 0;
		font-size: 0.85rem;
	}

	.link-btn.danger {
		color: #b91c1c;
	}

	.link-btn.danger.armed {
		font-weight: 600;
	}

	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.4);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 50;
	}

	.modal {
		background: white;
		border-radius: 8px;
		padding: 24px;
		width: 480px;
		max-width: 90vw;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.modal h2 {
		margin: 0;
		font-size: 1.2rem;
	}

	.modal form {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.modal label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.875rem;
	}

	.modal input {
		padding: 8px 10px;
		border: 1px solid var(--border, #e5e7eb);
		border-radius: 6px;
		font-size: 0.875rem;
	}

	.required {
		color: #b91c1c;
		font-style: normal;
		margin-left: 2px;
	}

	.hint {
		margin: 0;
		color: var(--text-secondary, #6b7280);
		font-size: 0.8rem;
	}

	.modal-footer {
		display: flex;
		gap: 8px;
		justify-content: flex-end;
		margin-top: 4px;
	}

	.btn-primary,
	.btn-cancel {
		padding: 8px 16px;
		border-radius: 6px;
		font-size: 0.875rem;
		cursor: pointer;
		border: 1px solid var(--border, #e5e7eb);
	}

	.btn-primary {
		background: var(--accent, #2563eb);
		color: white;
		border-color: var(--accent, #2563eb);
	}

	.btn-primary:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.btn-cancel {
		background: white;
	}
</style>
