import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

async function apiHeaders(page: import('@playwright/test').Page) {
	const token = await authToken(page);
	return { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme', 'Content-Type': 'application/json' };
}

interface ApprovalLevelConfig {
	min_amount: number | null;
	max_amount: number | null;
	approver_ids: string[];
	required_approvals: number;
	name: string;
	routing_rules: Array<{ field: string; operator: string; value: string | string[] }>;
	parallel_mode: 'any' | 'all';
	escalation_hours: number | null;
	escalation_to_user_ids: string[];
}

interface WorkflowResponse {
	id: string;
	steps_config: {
		steps: Array<{
			number: number;
			type: string;
			name: string;
			enabled: boolean;
			config: Record<string, unknown> & { approval_chain?: ApprovalLevelConfig[] };
		}>;
	};
}

async function createWorkflow(page: import('@playwright/test').Page, name: string): Promise<string> {
	const headers = await apiHeaders(page);
	const resp = await page.request.post(`${API_BASE}/api/workflows`, {
		headers,
		data: {
			name,
			steps: [
				{
					number: 1,
					type: 'approval',
					name: 'Approval',
					enabled: true,
					config: {
						required: true,
						approver_strategy: 'manual',
						approver_ids: [],
						approval_chain: [],
						require_segregation: true
					}
				}
			]
		}
	});
	expect(resp.status()).toBe(201);
	return ((await resp.json()) as { id: string }).id;
}

async function deleteWorkflow(page: import('@playwright/test').Page, id: string) {
	const headers = await apiHeaders(page);
	return page.request.delete(`${API_BASE}/api/workflows/${id}`, { headers });
}

async function getWorkflow(page: import('@playwright/test').Page, id: string): Promise<WorkflowResponse> {
	const headers = await apiHeaders(page);
	const resp = await page.request.get(`${API_BASE}/api/workflows/${id}`, { headers });
	return (await resp.json()) as WorkflowResponse;
}

async function patchWorkflow(
	page: import('@playwright/test').Page,
	id: string,
	body: unknown
) {
	const headers = await apiHeaders(page);
	return page.request.patch(`${API_BASE}/api/workflows/${id}`, { headers, data: body });
}

/**
 * Approval matrix UI — round-trips the new ApprovalLevelConfig fields
 * (routing_rules, parallel_mode, escalation_hours, escalation_to_user_ids)
 * through the same PATCH /api/workflows/{id} the editor uses, then
 * asserts the page renders the chain editor when strategy=chain.
 *
 * The pure-Python edges of the routing/escalation engine are covered in
 * backend/tests/test_approval_routing.py. This spec only proves the
 * persistence + UI surface.
 */

test.describe('/workflows/[id] — approval matrix editor (acme admin)', () => {
	let workflowId: string;

	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		workflowId = await createWorkflow(page, `e2e-matrix-${Date.now()}`);
	});

	test.afterEach(async ({ page }) => {
		if (workflowId) await deleteWorkflow(page, workflowId);
	});

	test('routing_rules + parallel_mode + escalation round-trip through PATCH', async ({
		page
	}) => {
		const wf = await getWorkflow(page, workflowId);
		const steps = structuredClone(wf.steps_config.steps);
		const approvalIdx = steps.findIndex((s) => s.type === 'approval');
		expect(approvalIdx).toBeGreaterThanOrEqual(0);

		const chain: ApprovalLevelConfig[] = [
			{
				name: 'IT < $5k',
				min_amount: null,
				max_amount: 5000,
				approver_ids: [],
				required_approvals: 1,
				parallel_mode: 'any',
				routing_rules: [
					{ field: 'department', operator: 'eq', value: 'IT' }
				],
				escalation_hours: 4,
				escalation_to_user_ids: []
			},
			{
				name: 'CFO + Finance Director',
				min_amount: 5000,
				max_amount: null,
				approver_ids: [],
				required_approvals: 2,
				parallel_mode: 'all',
				routing_rules: [
					{ field: 'gl_account', operator: 'in', value: ['6000', '6100'] }
				],
				escalation_hours: null,
				escalation_to_user_ids: []
			}
		];

		steps[approvalIdx].config = {
			...steps[approvalIdx].config,
			approver_strategy: 'chain',
			approval_chain: chain
		};

		const resp = await patchWorkflow(page, workflowId, { steps });
		expect(resp.status()).toBe(200);

		const after = await getWorkflow(page, workflowId);
		const savedChain = after.steps_config.steps[approvalIdx].config.approval_chain;
		expect(savedChain).toBeDefined();
		expect(savedChain).toHaveLength(2);
		expect(savedChain![0].routing_rules[0]).toEqual({
			field: 'department',
			operator: 'eq',
			value: 'IT'
		});
		expect(savedChain![0].parallel_mode).toBe('any');
		expect(savedChain![0].escalation_hours).toBe(4);
		expect(savedChain![1].parallel_mode).toBe('all');
		expect(savedChain![1].routing_rules[0].value).toEqual(['6000', '6100']);
	});

	test('matrix editor renders when strategy is set to chain', async ({ page }) => {
		await page.goto(`/workflows/${workflowId}`);

		// Wait for the workflow detail panel to settle.
		await expect(page.locator('select#approver-strategy')).toBeVisible();
		await page.locator('select#approver-strategy').selectOption('chain');

		// "Approval matrix" label and the empty-state add-level button render.
		await expect(page.getByText('Approval matrix', { exact: false })).toBeVisible();
		await expect(page.getByRole('button', { name: /add approval level/i })).toBeVisible();

		// Add a level — a level card with a name input must appear.
		await page.getByRole('button', { name: /add approval level/i }).click();
		const levelInput = page.locator('.level-name').first();
		await expect(levelInput).toBeVisible();
		await expect(levelInput).toHaveValue(/Level\s*1/);
	});
});
