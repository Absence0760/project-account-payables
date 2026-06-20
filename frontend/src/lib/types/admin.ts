export interface Role {
	id: string;
	name: string;
	description: string | null;
	is_system: boolean;
	// Effective granular permissions the role confers (catalog strings). For a
	// system role this is its static default set; for a custom role it's the
	// stored list. Optional for back-compat with older responses.
	permissions?: string[];
}

/** One row of the backend granular-permission catalog (GET /api/admin/permissions). */
export interface PermissionCatalogEntry {
	key: string;
	label: string;
}

// Permission keys — mirror `backend/app/api/permissions.py::ALL_PERMISSIONS`.
// Referenced by `auth.can(PERM_*)` at the gated controls so a typo is caught.
export const PERM_INVOICE_APPROVE = 'invoice.approve';
export const PERM_PAYMENT_RUN_APPROVE = 'payment_run.approve';
export const PERM_PAYMENT_EXECUTE = 'payment.execute';
export const PERM_PAYMENT_VOID = 'payment.void';
export const PERM_VENDOR_BANK_CHANGE_APPROVE = 'vendor.bank_change.approve';
export const PERM_VENDOR_BLOCK = 'vendor.block';
export const PERM_VENDOR_MANAGE = 'vendor.manage';
export const PERM_USER_MANAGE = 'user.manage';

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
