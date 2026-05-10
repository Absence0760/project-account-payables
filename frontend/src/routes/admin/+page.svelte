<script lang="ts">
	import type { AdminUser } from '$lib/types/admin';
	import { ROLE_LABELS } from '$lib/types/admin';
	import { adminStore } from '$lib/stores/admin.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import BulkBar from '$lib/components/BulkBar.svelte';
	import BulkDeleteButton from '$lib/components/BulkDeleteButton.svelte';
	import SearchBox from '$lib/components/SearchBox.svelte';
	import { toast } from '$lib/components/Toast.svelte';

	let showCreateModal = $state(false);
	let editingUser = $state<AdminUser | null>(null);
	let createdCredentials = $state<{ email: string; password: string } | null>(null);
	let confirmDeleteId = $state<string | null>(null);

	// Bulk-select state
	let selectedIds = $state<Set<string>>(new Set());
	let bulkDeleting = $state(false);

	// Create form
	let newName = $state('');
	let newEmail = $state('');
	let newRoles = $state<string[]>([]);

	// Edit form
	let editName = $state('');
	let editEmail = $state('');
	let editRoles = $state<string[]>([]);
	let editPassword = $state('');

	let saving = $state(false);

	let search = $state('');
	let searchTimer: ReturnType<typeof setTimeout> | null = null;

	$effect(() => {
		adminStore.fetchUsers();
		adminStore.fetchRoles();
	});

	$effect(() => {
		// React to search changes; debounce to avoid hammering the API.
		const q = search;
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			adminStore.fetchUsers({ search: q });
		}, 250);
	});

	async function loadMore() {
		await adminStore.loadMoreUsers({ search });
	}

	let selectableIds = $derived(
		adminStore.users.filter((u) => u.id !== auth.user?.id).map((u) => u.id)
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
		if (allSelected) {
			selectedIds = new Set();
		} else {
			selectedIds = new Set(selectableIds);
		}
	}

	async function handleBulkDelete() {
		if (selectedIds.size === 0) return;
		bulkDeleting = true;
		try {
			const result = await adminStore.bulkDeleteUsers([...selectedIds]);
			selectedIds = new Set();
			if (result.failed.length === 0) {
				toast(`Deleted ${result.deleted.length} user${result.deleted.length === 1 ? '' : 's'}`, 'success');
			} else if (result.deleted.length === 0) {
				const reason = describeBulkFailure(result.failed[0]);
				toast(`No users deleted — ${reason}`, 'error');
			} else {
				toast(
					`Deleted ${result.deleted.length}; ${result.failed.length} blocked (in-flight references)`,
					'success',
				);
			}
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Bulk delete failed', 'error');
		} finally {
			bulkDeleting = false;
		}
	}

	function describeBulkFailure(f: { reason: string; references: { open_invoice_assignments: number; pending_approval_steps: number; active_workflow_approver_in: number } | null }): string {
		if (f.reason === 'self') return 'cannot delete yourself';
		if (f.reason === 'not_found') return 'user not found';
		if (f.reason === 'blocked' && f.references) {
			const parts: string[] = [];
			if (f.references.open_invoice_assignments) parts.push(`${f.references.open_invoice_assignments} open invoice${f.references.open_invoice_assignments === 1 ? '' : 's'}`);
			if (f.references.pending_approval_steps) parts.push(`${f.references.pending_approval_steps} pending approval${f.references.pending_approval_steps === 1 ? '' : 's'}`);
			if (f.references.active_workflow_approver_in) parts.push(`${f.references.active_workflow_approver_in} active workflow${f.references.active_workflow_approver_in === 1 ? '' : 's'}`);
			return `still referenced by ${parts.join(', ')}`;
		}
		return 'blocked';
	}

	function openCreate() {
		newName = '';
		newEmail = '';
		newRoles = [];
		showCreateModal = true;
	}

	function openEdit(user: AdminUser) {
		editingUser = user;
		editName = user.full_name;
		editEmail = user.email;
		editRoles = user.roles.map((r) => r.name);
		editPassword = '';
	}

	async function handleCreate() {
		if (!newName.trim() || !newEmail.trim()) return;
		saving = true;
		try {
			const result = await adminStore.createUser({
				full_name: newName.trim(),
				email: newEmail.trim(),
				role_names: newRoles,
			});
			showCreateModal = false;
			createdCredentials = {
				email: result.email,
				password: (result as unknown as { temporary_password: string }).temporary_password,
			};
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to create user', 'error');
		} finally {
			saving = false;
		}
	}

	async function handleUpdate() {
		if (!editingUser) return;
		saving = true;
		try {
			const changes: Record<string, unknown> = {
				full_name: editName.trim(),
				email: editEmail.trim(),
				role_names: editRoles,
			};
			if (editPassword.trim()) {
				changes.password = editPassword;
			}
			await adminStore.updateUser(editingUser.id, changes);
			toast('User updated', 'success');
			editingUser = null;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to update user', 'error');
		} finally {
			saving = false;
		}
	}

	async function toggleActive(user: AdminUser) {
		try {
			await adminStore.updateUser(user.id, { is_active: !user.is_active });
			toast(user.is_active ? 'User deactivated' : 'User activated', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to update user', 'error');
		}
	}

	async function handleDelete(id: string) {
		try {
			await adminStore.deleteUser(id);
			toast('User deleted', 'success');
			confirmDeleteId = null;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to delete user', 'error');
		}
	}

	function toggleRole(role: string, list: string[]): string[] {
		return list.includes(role) ? list.filter((r) => r !== role) : [...list, role];
	}

	function formatDate(iso: string): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
	}

	function handleWindowClick(e: MouseEvent) {
		if (confirmDeleteId && !(e.target as HTMLElement).closest('.delete-btn')) {
			confirmDeleteId = null;
		}
	}

	let isSelf = (id: string) => id === auth.user?.id;
</script>

<svelte:window onclick={handleWindowClick} />

<div class="workspace">
	<header class="toolbar">
		<h1>Users</h1>
		<div class="toolbar-actions">
			<SearchBox
				bind:value={search}
				placeholder="Search name or email..."
				ariaLabel="Search users"
			/>
			<button class="btn-primary" onclick={openCreate}>+ Invite User</button>
		</div>
	</header>

	<BulkBar count={selectedIds.size} onclear={() => (selectedIds = new Set())}>
		{#snippet actions()}
			<BulkDeleteButton
				onconfirm={handleBulkDelete}
				disabled={bulkDeleting}
				label={`Delete ${selectedIds.size}`}
			/>
		{/snippet}
	</BulkBar>

	<div class="grid-container">
		<table>
			<thead>
				<tr>
					<th class="checkbox-col">
						<input
							type="checkbox"
							checked={allSelected}
							onchange={toggleSelectAll}
							aria-label="Select all users"
						/>
					</th>
					<th>Name</th>
					<th>Email</th>
					<th>Roles</th>
					<th>Status</th>
					<th>Created</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each adminStore.users as user (user.id)}
					<tr class:inactive={!user.is_active} class:row-selected={selectedIds.has(user.id)}>
						<td class="checkbox-col">
							{#if !isSelf(user.id)}
								<input
									type="checkbox"
									checked={selectedIds.has(user.id)}
									onchange={() => toggleSelect(user.id)}
									aria-label="Select {user.full_name}"
								/>
							{/if}
						</td>
						<td class="name-cell">
							{user.full_name}
							{#if isSelf(user.id)}
								<span class="you-badge">You</span>
							{/if}
						</td>
						<td class="email-cell">{user.email}</td>
						<td>
							<div class="role-badges">
								{#each user.roles as role}
									<span class="role-badge">{ROLE_LABELS[role.name] ?? role.name}</span>
								{:else}
									<span class="no-roles">No roles</span>
								{/each}
							</div>
						</td>
						<td>
							<span class="status-dot" class:active={user.is_active} class:deactivated={!user.is_active}>
								{user.is_active ? 'Active' : 'Inactive'}
							</span>
						</td>
						<td class="date-cell">{formatDate(user.created_at)}</td>
						<td class="actions">
							<button class="edit-btn" onclick={() => openEdit(user)}>Edit</button>
							<button
								class="toggle-btn"
								class:deactivate={user.is_active}
								onclick={() => toggleActive(user)}
							>
								{user.is_active ? 'Deactivate' : 'Activate'}
							</button>
							{#if !isSelf(user.id)}
								<button
									class="delete-btn"
									class:armed={confirmDeleteId === user.id}
									onclick={(e) => {
										e.stopPropagation();
										if (confirmDeleteId === user.id) {
											handleDelete(user.id);
										} else {
											confirmDeleteId = user.id;
										}
									}}
								>
									{#if confirmDeleteId === user.id}
										<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
									{:else}
										<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
									{/if}
								</button>
							{/if}
						</td>
					</tr>
				{:else}
					<tr>
						<td colspan="7" class="empty">No users found.</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

	{#if adminStore.hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMore} disabled={adminStore.loading}>
				{adminStore.loading ? 'Loading…' : `Load more (${adminStore.users.length} of ${adminStore.total})`}
			</button>
		</div>
	{:else if adminStore.total > 0}
		<div class="load-more-row">
			<span class="load-more-end">Showing all {adminStore.total} user{adminStore.total === 1 ? '' : 's'}</span>
		</div>
	{/if}
</div>

<!-- Invite User Modal -->
{#if showCreateModal}
	<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) showCreateModal = false; }}>
		<div class="modal" role="dialog" aria-label="Invite user">
			<h2>Invite User</h2>
			<p class="modal-hint">A temporary password will be generated automatically.</p>
			<form onsubmit={(e) => { e.preventDefault(); handleCreate(); }}>
				<label>
					<span>Full Name <em class="required">*</em></span>
					<input type="text" bind:value={newName} required />
				</label>
				<label>
					<span>Email <em class="required">*</em></span>
					<input type="email" bind:value={newEmail} required />
				</label>
				<fieldset>
					<legend>Roles</legend>
					<div class="role-checks">
						{#each adminStore.roles as role}
							<label class="check-label">
								<input
									type="checkbox"
									checked={newRoles.includes(role.name)}
									onchange={() => (newRoles = toggleRole(role.name, newRoles))}
								/>
								{ROLE_LABELS[role.name] ?? role.name}
							</label>
						{/each}
					</div>
				</fieldset>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={() => (showCreateModal = false)}>Cancel</button>
					<button type="submit" class="btn-primary" disabled={saving}>
						{saving ? 'Creating...' : 'Create User'}
					</button>
				</div>
			</form>
		</div>
	</div>
{/if}

<!-- Created Credentials Modal -->
{#if createdCredentials}
	<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) createdCredentials = null; }}>
		<div class="modal" role="dialog" aria-label="User created">
			<h2>User Created</h2>
			<p class="modal-hint">Share these credentials with the new user. The password is shown only once.</p>
			<div class="credentials-box">
				<div class="credential-row">
					<span class="credential-label">Email</span>
					<code class="credential-value">{createdCredentials.email}</code>
				</div>
				<div class="credential-row">
					<span class="credential-label">Temporary Password</span>
					<code class="credential-value password">{createdCredentials.password}</code>
				</div>
			</div>
			<div class="modal-footer">
				<button class="btn-primary" onclick={() => {
					navigator.clipboard.writeText(`Email: ${createdCredentials?.email}\nPassword: ${createdCredentials?.password}`);
					toast('Copied to clipboard', 'success');
				}}>Copy to Clipboard</button>
				<button class="btn-cancel" onclick={() => (createdCredentials = null)}>Done</button>
			</div>
		</div>
	</div>
{/if}

<!-- Edit User Modal -->
{#if editingUser}
	<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) editingUser = null; }}>
		<div class="modal" role="dialog" aria-label="Edit user">
			<h2>Edit User</h2>
			<form onsubmit={(e) => { e.preventDefault(); handleUpdate(); }}>
				<label>
					<span>Full Name</span>
					<input type="text" bind:value={editName} required />
				</label>
				<label>
					<span>Email</span>
					<input type="email" bind:value={editEmail} required />
				</label>
				<label>
					<span>Reset Password <em class="hint">(leave blank to keep current)</em></span>
					<input type="password" bind:value={editPassword} minlength="6" />
				</label>
				<fieldset>
					<legend>Roles</legend>
					<div class="role-checks">
						{#each adminStore.roles as role}
							<label class="check-label">
								<input
									type="checkbox"
									checked={editRoles.includes(role.name)}
									onchange={() => (editRoles = toggleRole(role.name, editRoles))}
								/>
								{ROLE_LABELS[role.name] ?? role.name}
							</label>
						{/each}
					</div>
				</fieldset>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={() => (editingUser = null)}>Cancel</button>
					<button type="submit" class="btn-primary" disabled={saving}>
						{saving ? 'Saving...' : 'Save Changes'}
					</button>
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
		gap: 16px;
		min-height: 100vh;
	}

	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	h1 {
		font-size: 1.3rem;
		font-weight: 700;
		margin: 0;
	}

	.btn-primary {
		padding: 8px 18px;
		border-radius: 6px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
	}

	.btn-primary:hover:not(:disabled) {
		opacity: 0.85;
	}

	.btn-primary:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.toolbar-actions {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.load-more-row {
		display: flex;
		justify-content: center;
		padding: 8px 0 4px;
	}

	.btn-load-more {
		padding: 8px 18px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-load-more:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}

	.btn-load-more:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.load-more-end {
		font-size: 0.78rem;
		color: var(--text-muted);
	}

	/* --- Bulk-row checkbox column (BulkBar / BulkDeleteButton come from $lib/components) --- */

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

	tbody tr.row-selected {
		background: rgba(99, 140, 255, 0.08);
	}

	/* --- Table --- */

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
	}

	tr:last-child td {
		border-bottom: none;
	}

	tbody tr:hover {
		background: rgba(99, 140, 255, 0.04);
	}

	.inactive td {
		opacity: 0.5;
	}

	.name-cell {
		font-weight: 500;
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.you-badge {
		font-size: 0.68rem;
		font-weight: 600;
		padding: 1px 6px;
		border-radius: 8px;
		background: rgba(99, 140, 255, 0.12);
		color: var(--accent);
	}

	.email-cell {
		color: var(--text-muted);
	}

	.date-cell {
		color: var(--text-muted);
		font-size: 0.82rem;
		white-space: nowrap;
	}

	.empty {
		text-align: center;
		padding: 40px 14px;
		color: var(--text-muted);
	}

	/* --- Roles --- */

	.role-badges {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}

	.role-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		background: rgba(99, 140, 255, 0.1);
		color: var(--accent);
		font-size: 0.75rem;
		font-weight: 500;
		white-space: nowrap;
	}

	.no-roles {
		font-size: 0.78rem;
		color: var(--text-muted);
		font-style: italic;
	}

	/* --- Status --- */

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

	.status-dot.deactivated::before {
		background: #e04040;
	}

	/* --- Actions --- */

	.actions {
		display: flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
	}

	.edit-btn,
	.toggle-btn {
		padding: 4px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.edit-btn:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.toggle-btn.deactivate:hover {
		border-color: #e04040;
		color: #e04040;
	}

	.toggle-btn:not(.deactivate):hover {
		border-color: #1fa86a;
		color: #1fa86a;
	}

	.delete-btn {
		display: grid;
		place-items: center;
		width: 30px;
		height: 28px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		cursor: pointer;
		transition: all 0.15s;
	}

	.delete-btn:hover {
		border-color: #e04040;
		color: #e04040;
	}

	.delete-btn.armed {
		border-color: #e04040;
		background: rgba(224, 64, 64, 0.1);
		color: #e04040;
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

	.modal input[type='text'],
	.modal input[type='email'],
	.modal input[type='password'] {
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

	fieldset {
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 10px 12px;
		margin: 0;
	}

	legend {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
		padding: 0 4px;
	}

	.role-checks {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
	}

	.check-label {
		display: flex;
		flex-direction: row;
		align-items: center;
		gap: 6px;
		font-size: 0.85rem;
		color: var(--text);
		cursor: pointer;
	}

	.check-label input[type='checkbox'] {
		accent-color: var(--accent);
		cursor: pointer;
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
	}

	.required {
		color: #e04040;
		font-style: normal;
	}

	.hint {
		font-style: italic;
		text-transform: none;
		letter-spacing: normal;
		font-weight: 400;
	}

	/* --- Credentials display --- */

	.credentials-box {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 14px;
		margin-bottom: 16px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.credential-row {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}

	.credential-label {
		font-size: 0.72rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.credential-value {
		font-size: 0.9rem;
		color: var(--text);
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
		background: var(--surface);
		padding: 4px 8px;
		border-radius: 4px;
		border: 1px solid var(--border);
		user-select: all;
	}

	.credential-value.password {
		color: var(--accent);
		font-weight: 600;
	}
</style>
