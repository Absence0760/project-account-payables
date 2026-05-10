import type { AdminUser, Role } from '$lib/types/admin';
import { api } from '$lib/api';

interface AdminUserListResponse {
	items: AdminUser[];
	total: number;
}

interface CreateUserResponse extends AdminUser {
	temporary_password: string;
}

function createAdminStore() {
	let users = $state<AdminUser[]>([]);
	let roles = $state<Role[]>([]);
	let loading = $state(false);

	async function fetchUsers() {
		loading = true;
		try {
			const res = await api.get<AdminUserListResponse>('/api/admin/users');
			users = res.items;
		} finally {
			loading = false;
		}
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
		return result;
	}

	return {
		get users() { return users; },
		get roles() { return roles; },
		get loading() { return loading; },
		fetchUsers,
		fetchRoles,
		createUser,
		updateUser,
		deleteUser,
		bulkDeleteUsers,
	};
}

export const adminStore = createAdminStore();
