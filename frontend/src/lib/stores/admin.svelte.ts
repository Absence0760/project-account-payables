import type { AdminUser, Role } from '$lib/types/admin';
import { api } from '$lib/api';

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
	let loading = $state(false);
	let total = $state(0);
	let page = $state(1);
	let pageSize = $state(20);

	async function fetchUsers(opts: FetchUsersOptions = {}) {
		loading = true;
		try {
			const params = new URLSearchParams();
			if (opts.search) params.set('search', opts.search);
			const nextPage = opts.page ?? 1;
			params.set('page', String(nextPage));
			params.set('page_size', String(opts.pageSize ?? pageSize));
			const res = await api.get<AdminUserListResponse>(`/api/admin/users?${params}`);
			users = opts.append ? [...users, ...res.items] : res.items;
			total = res.total;
			page = nextPage;
			if (opts.pageSize) pageSize = opts.pageSize;
		} finally {
			loading = false;
		}
	}

	async function loadMoreUsers(opts: { search?: string } = {}) {
		await fetchUsers({ ...opts, page: page + 1, append: true });
	}

	async function fetchRoles() {
		try {
			roles = await api.get<Role[]>('/api/admin/roles');
		} catch {
			// non-critical
		}
	}

	async function createUser(data: {
		email: string;
		full_name: string;
		role_names: string[];
	}): Promise<CreateUserResponse> {
		const created = await api.post<CreateUserResponse>('/api/admin/users', data);
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
		users = users.map((u) => (u.id === id ? updated : u));
		return updated;
	}

	async function deleteUser(id: string): Promise<void> {
		await api.delete(`/api/admin/users/${id}`);
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
		users = users.filter((u) => !deletedSet.has(u.id));
		total = Math.max(0, total - result.deleted.length);
		return result;
	}

	return {
		get users() { return users; },
		get roles() { return roles; },
		get loading() { return loading; },
		get total() { return total; },
		get page() { return page; },
		get pageSize() { return pageSize; },
		get hasMore() { return users.length < total; },
		fetchUsers,
		loadMoreUsers,
		fetchRoles,
		createUser,
		updateUser,
		deleteUser,
		bulkDeleteUsers,
	};
}

export const adminStore = createAdminStore();
