import { resolve } from 'node:path';

/**
 * Seeded users referenced by Playwright specs.
 *
 * UUIDs are pinned in the project's seed (wherever that lands —
 * `seed.sql`, a TS seed script, supabase seed, etc.) so the test
 * files can reference them by literal — no `select id from users
 * where email = ?` lookups in tests, which would create a hidden
 * dependency on the seed running before the test fixture.
 *
 * If you change a UUID here you must change it in the seed too — and
 * vice-versa.
 */

export type SeededUser = {
	email: string;
	password: string;
	id: string;
	role: 'admin' | 'approver' | 'clerk';
	tenantId: string;
	/// Absolute path; populated by globalSetup, then read by spec
	/// files via test.use({ storageState }). Absolute so the path
	/// resolves identically regardless of Playwright's cwd.
	storageStatePath: string;
};

// .auth/ lives next to playwright.config.ts (one level up from this
// fixtures/ directory). Resolve once at module-load.
const STORAGE_DIR = resolve(import.meta.dirname, '..', '.auth');

// Two tenants, three roles per tenant — enough to assert tenant
// isolation (admin@tenant-a cannot see tenant-b's invoices) and role
// gates (clerk cannot approve, approver can approve, admin can do
// either).
//
// UUIDs are placeholders. Replace with real seed UUIDs once the
// seed file lands.
export const TENANT_A_ID = '00000000-0000-0000-0000-00000000000a';
export const TENANT_B_ID = '00000000-0000-0000-0000-00000000000b';

export const ADMIN_A: SeededUser = {
	email: 'admin-a@test.local',
	password: 'testtest',
	id: 'a1b2c3d4-0000-0000-0000-000000000001',
	role: 'admin',
	tenantId: TENANT_A_ID,
	storageStatePath: resolve(STORAGE_DIR, 'admin-a.json')
};

export const APPROVER_A: SeededUser = {
	email: 'approver-a@test.local',
	password: 'testtest',
	id: 'a1b2c3d4-0000-0000-0000-000000000002',
	role: 'approver',
	tenantId: TENANT_A_ID,
	storageStatePath: resolve(STORAGE_DIR, 'approver-a.json')
};

export const CLERK_A: SeededUser = {
	email: 'clerk-a@test.local',
	password: 'testtest',
	id: 'a1b2c3d4-0000-0000-0000-000000000003',
	role: 'clerk',
	tenantId: TENANT_A_ID,
	storageStatePath: resolve(STORAGE_DIR, 'clerk-a.json')
};

export const ADMIN_B: SeededUser = {
	email: 'admin-b@test.local',
	password: 'testtest',
	id: 'b1b2c3d4-0000-0000-0000-000000000001',
	role: 'admin',
	tenantId: TENANT_B_ID,
	storageStatePath: resolve(STORAGE_DIR, 'admin-b.json')
};

export const ALL_USERS: SeededUser[] = [ADMIN_A, APPROVER_A, CLERK_A, ADMIN_B];
