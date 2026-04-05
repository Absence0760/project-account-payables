export interface Role {
	id: string;
	name: string;
	description: string | null;
}

export interface AdminUser {
	id: string;
	email: string;
	full_name: string;
	is_active: boolean;
	roles: Role[];
	created_at: string;
}

export const ROLE_LABELS: Record<string, string> = {
	admin: 'Admin',
	ap_manager: 'AP Manager',
	ap_clerk: 'AP Clerk',
	cfo: 'CFO'
};
