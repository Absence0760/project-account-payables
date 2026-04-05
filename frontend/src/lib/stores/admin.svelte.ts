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

	return {
		get users() { return users; },
		get roles() { return roles; },
		get loading() { return loading; },
		fetchUsers,
		fetchRoles,
		createUser,
		updateUser,
		deleteUser,
	};
}

export const adminStore = createAdminStore();
