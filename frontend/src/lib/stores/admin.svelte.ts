import type { AdminUser, PermissionCatalogEntry, Role } from '$lib/types/admin';
import { api } from '$lib/api';
import { appendUnique } from '$lib/utils/pagination';
import { createRequestSequencer } from '$lib/utils/requestSequence';

interface AdminUserListResponse {
	items: AdminUser[];
	total: number;
}

interface CreateUserResponse extends AdminUser {
	temporary_password: string;
}

interface FetchUsersOptions {
	search?: string;
	page?: number;
	pageSize?: number;
	append?: boolean;
}

function createAdminStore() {
	let users = $state<AdminUser[]>([]);
	let roles = $state<Role[]>([]);
	let permissionCatalog = $state<PermissionCatalogEntry[]>([]);
	let loading = $state(false);
	let total = $state(0);
	let page = $state(1);
	let pageSize = $state(20);

	// Users and roles are two independent lists loaded by two independent
	// requests, so they get a sequencer each — a roles refresh must not mark an
	// in-flight users fetch un-committable, or vice versa. Within each, the
	// create/update/delete helpers below mutate the list in place with no fetch
	// of their own, so they retire whatever is in flight before they write.
	// See `frontend/CLAUDE.md` § Sequencing list fetches.
	const usersSequence = createRequestSequencer();
	const rolesSequence = createRequestSequencer();

	async function fetchUsers(opts: FetchUsersOptions = {}) {
		const token = usersSequence.start();
		loading = true;
		try {
			const params = new URLSearchParams();
			if (opts.search) params.set('search', opts.search);
			const nextPage = opts.page ?? 1;
			params.set('page', String(nextPage));
			params.set('page_size', String(opts.pageSize ?? pageSize));
			const res = await api.get<AdminUserListResponse>(`/api/admin/users?${params}`);
			// Superseded by a newer search/page fetch, or by a local edit.
			if (!usersSequence.canCommit(token)) return;
			users = opts.append ? appendUnique(users, res.items) : res.items;
			total = res.total;
			page = nextPage;
			if (opts.pageSize) pageSize = opts.pageSize;
		} finally {
			if (usersSequence.isCurrentRequest(token)) loading = false;
		}
	}

	async function loadMoreUsers(opts: { search?: string } = {}) {
		await fetchUsers({ ...opts, page: page + 1, append: true });
	}

	async function fetchRoles() {
		const token = rolesSequence.start();
		try {
			const fetched = await api.get<Role[]>('/api/admin/roles');
			if (!rolesSequence.canCommit(token)) return;
			roles = fetched;
		} catch {
			// non-critical
		}
	}

	async function fetchPermissionCatalog() {
		// Cache for the session — the catalog is static. Refetch only if empty.
		if (permissionCatalog.length > 0) return;
		try {
			permissionCatalog = await api.get<PermissionCatalogEntry[]>('/api/admin/permissions');
		} catch {
			// non-critical — the role editor degrades to no checkboxes
		}
	}

	async function createUser(data: {
		email: string;
		full_name: string;
		role_names: string[];
	}): Promise<CreateUserResponse> {
		const created = await api.post<CreateUserResponse>('/api/admin/users', data);
		// A users fetch already in flight read the list BEFORE this user existed,
		// so its response would drop the new row. An invite needs no pre-existing
		// row, so it races even the page's very first load.
		usersSequence.supersedeInFlight();
		users = [created, ...users];
		total += 1;
		return created;
	}

	async function updateUser(
		id: string,
		changes: Partial<{
			full_name: string;
			email: string;
			is_active: boolean;
			role_names: string[];
			password: string;
		}>
	): Promise<AdminUser> {
		const updated = await api.patch<AdminUser>(`/api/admin/users/${id}`, changes);
		usersSequence.supersedeInFlight();
		users = users.map((u) => (u.id === id ? updated : u));
		return updated;
	}

	async function deleteUser(id: string): Promise<void> {
		await api.delete(`/api/admin/users/${id}`);
		usersSequence.supersedeInFlight();
		users = users.filter((u) => u.id !== id);
		total = Math.max(0, total - 1);
	}

	interface BulkDeleteFailure {
		user_id: string;
		reason: 'not_found' | 'self' | 'blocked';
		references: {
			open_invoice_assignments: number;
			pending_approval_steps: number;
			active_workflow_approver_in: number;
		} | null;
	}
	interface BulkDeleteResult {
		deleted: string[];
		failed: BulkDeleteFailure[];
	}

	async function bulkDeleteUsers(ids: string[]): Promise<BulkDeleteResult> {
		const result = await api.post<BulkDeleteResult>('/api/admin/users/bulk-delete', {
			user_ids: ids
		});
		const deletedSet = new Set(result.deleted);
		usersSequence.supersedeInFlight();
		users = users.filter((u) => !deletedSet.has(u.id));
		total = Math.max(0, total - result.deleted.length);
		return result;
	}

	async function createRole(data: {
		name: string;
		description?: string;
		permissions?: string[];
	}): Promise<Role> {
		const created = await api.post<Role>('/api/admin/roles', data);
		rolesSequence.supersedeInFlight();
		roles = [...roles, created];
		return created;
	}

	async function updateRole(
		id: string,
		changes: { description?: string; permissions?: string[] }
	): Promise<Role> {
		const updated = await api.patch<Role>(`/api/admin/roles/${id}`, changes);
		rolesSequence.supersedeInFlight();
		roles = roles.map((r) => (r.id === id ? updated : r));
		return updated;
	}

	async function deleteRole(id: string): Promise<void> {
		await api.delete(`/api/admin/roles/${id}`);
		rolesSequence.supersedeInFlight();
		roles = roles.filter((r) => r.id !== id);
	}

	return {
		get users() { return users; },
		get roles() { return roles; },
		get permissionCatalog() { return permissionCatalog; },
		get loading() { return loading; },
		get total() { return total; },
		get page() { return page; },
		get pageSize() { return pageSize; },
		get hasMore() { return users.length < total; },
		fetchUsers,
		loadMoreUsers,
		fetchRoles,
		fetchPermissionCatalog,
		createUser,
		updateUser,
		deleteUser,
		bulkDeleteUsers,
		createRole,
		updateRole,
		deleteRole,
	};
}

export const adminStore = createAdminStore();
