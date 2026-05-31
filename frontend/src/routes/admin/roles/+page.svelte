<script lang="ts">
	import { adminStore } from '$lib/stores/admin.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import type { Role } from '$lib/types/admin';

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

<div class="workspace">
	<header class="toolbar">
		<h1>Roles</h1>
		<div class="toolbar-actions">
			<button class="btn-primary" onclick={() => (creating = true)}>+ Create Role</button>
		</div>
	</header>

	<section class="role-section">
		<div class="section-header">
			<h2>System roles</h2>
			<p class="section-hint">
				Built-in roles gate hardcoded routes and cannot be edited or deleted.
			</p>
		</div>
		<div class="grid-container">
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
							<td>
								<span class="role-badge system">{role.name}</span>
							</td>
							<td class="muted-cell">{role.description ?? '—'}</td>
						</tr>
					{:else}
						<tr><td colspan="2" class="empty">No system roles configured.</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>

	<section class="role-section">
		<div class="section-header">
			<h2>Custom roles</h2>
			<p class="section-hint">
				Mint additional role names for this organization. Custom roles can be assigned to
				users and referenced in approval-chain configuration.
			</p>
		</div>
		<div class="grid-container">
			<table>
				<thead>
					<tr>
						<th>Name</th>
						<th>Description</th>
						<th class="actions-col"></th>
					</tr>
				</thead>
				<tbody>
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
					{:else}
						<tr><td colspan="3" class="empty">No custom roles yet.</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
</div>

{#if creating}
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) creating = false; }}>
		<div class="modal" role="dialog" aria-label="Create role">
			<h2>Create role</h2>
			<p class="modal-hint">
				The name is referenced by approval-chain configs once it's been created — pick
				something stable.
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
		</div>
	</div>
{/if}

{#if editing}
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) editing = null; }}>
		<div class="modal" role="dialog" aria-label="Edit role">
			<h2>Edit "{editing.name}"</h2>
			<p class="modal-hint">
				Role names are immutable — they're referenced by approval-chain configs. Edit the
				description only.
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
		</div>
	</div>
{/if}

<style>
	.workspace {
		max-width: 1800px;
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

	.toolbar h1 {
		margin: 0;
		font-size: 1.4rem;
		font-weight: 600;
		color: var(--text);
	}

	.toolbar-actions {
		display: flex;
		gap: 8px;
	}

	.btn-primary {
		padding: 8px 18px;
		border-radius: 4px;
		border: 1px solid var(--accent);
		background: var(--accent);
		color: white;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-primary:hover {
		filter: brightness(1.1);
	}

	.btn-primary:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

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

	.grid-container {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow-x: auto;
		min-width: 0;
		max-width: 100%;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.875rem;
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
		padding: 10px 14px;
		border-bottom: 1px solid var(--border);
		color: var(--text);
		vertical-align: middle;
	}

	tr:last-child td {
		border-bottom: none;
	}

	tbody tr:hover {
		background: rgba(99, 140, 255, 0.04);
	}

	.muted-cell {
		color: var(--text-muted);
	}

	.empty {
		text-align: center;
		padding: 28px 14px;
		color: var(--text-muted);
		font-style: italic;
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

	.actions-col {
		width: 160px;
	}

	.actions {
		display: flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
	}

	/* --- Modal --- */

	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: grid;
		place-items: center;
		z-index: 100;
		backdrop-filter: blur(2px);
	}

	.modal {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		width: min(440px, 92vw);
		padding: 24px;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.3);
	}

	.modal h2 {
		margin: 0 0 4px;
		font-size: 1.1rem;
		font-weight: 600;
		color: var(--text);
	}

	.modal-hint {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0 0 16px;
	}

	.modal form {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}

	.modal label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.modal label span {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.modal input[type='text'] {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.88rem;
		color: var(--text);
		font-family: inherit;
		width: 100%;
		box-sizing: border-box;
	}

	.modal input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.modal-footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding-top: 8px;
		border-top: 1px solid var(--border);
	}

	.btn-cancel {
		padding: 8px 18px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-cancel:hover {
		background: var(--bg);
		color: var(--text);
	}
</style>
