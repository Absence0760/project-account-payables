import type {
	WorkflowDefinition,
	WorkflowStep,
	WorkflowTemplate,
	WorkflowVersion,
	WorkflowDiff,
	SimInvoice,
	SimulationResult,
	WorkflowExport,
} from '$lib/types/workflow';
import { api } from '$lib/api';

export interface ApprovalConfig {
	approver_strategy: 'manual' | 'specific' | 'auto';
	approver_ids: string[];
}

export interface ActiveSteps {
	extraction: boolean;
	approval: boolean;
	erp_export: boolean;
	approval_config: ApprovalConfig | null;
}

const DEFAULT_ACTIVE_STEPS: ActiveSteps = {
	extraction: false,
	approval: false,
	erp_export: false,
	approval_config: null,
};

const PAGE_SIZE = 20;

interface WorkflowListResponse {
	items: WorkflowDefinition[];
	total: number;
	page: number;
	page_size: number;
}

function createWorkflowStore() {
	let workflows = $state<WorkflowDefinition[]>([]);
	let loading = $state(false);
	let total = $state(0);
	let page = $state(1);
	let activeSteps = $state<ActiveSteps>({ ...DEFAULT_ACTIVE_STEPS });

	async function load(opts: { append?: boolean; nextPage?: number } = {}) {
		const nextPage = opts.nextPage ?? 1;
		loading = true;
		try {
			const res = await api.get<WorkflowListResponse>(
				`/api/workflows?page=${nextPage}&page_size=${PAGE_SIZE}`
			);
			workflows = opts.append ? [...workflows, ...res.items] : res.items;
			total = res.total;
			page = nextPage;
		} finally {
			loading = false;
		}
	}

	async function fetch() {
		await load();
	}

	async function loadMore() {
		await load({ append: true, nextPage: page + 1 });
	}

	async function getById(id: string): Promise<WorkflowDefinition> {
		return api.get<WorkflowDefinition>(`/api/workflows/${id}`);
	}

	async function create(data: {
		name: string;
		description?: string;
		steps: WorkflowStep[];
	}): Promise<WorkflowDefinition> {
		const created = await api.post<WorkflowDefinition>('/api/workflows', data);
		workflows = [...workflows, created];
		total += 1;
		return created;
	}

	async function update(
		id: string,
		changes: { name?: string; description?: string; is_active?: boolean; steps?: WorkflowStep[] }
	): Promise<WorkflowDefinition> {
		const updated = await api.patch<WorkflowDefinition>(`/api/workflows/${id}`, changes);
		workflows = workflows.map((w) => (w.id === id ? updated : w));
		return updated;
	}

	async function remove(id: string): Promise<void> {
		await api.delete(`/api/workflows/${id}`);
		workflows = workflows.filter((w) => w.id !== id);
		total = Math.max(0, total - 1);
	}

	interface BulkDeleteFailure {
		workflow_id: string;
		reason: 'not_found' | 'default' | 'active' | 'instances';
		instance_count: number | null;
	}
	interface BulkDeleteResult {
		deleted: string[];
		failed: BulkDeleteFailure[];
	}

	async function bulkRemove(ids: string[]): Promise<BulkDeleteResult> {
		const result = await api.post<BulkDeleteResult>('/api/workflows/bulk-delete', {
			workflow_ids: ids
		});
		const deletedSet = new Set(result.deleted);
		workflows = workflows.filter((w) => !deletedSet.has(w.id));
		total = Math.max(0, total - deletedSet.size);
		return result;
	}

	// ── No-code builder: templates, versioning, simulation, import/export ──

	async function listTemplates(): Promise<WorkflowTemplate[]> {
		const res = await api.get<{ items: WorkflowTemplate[] }>('/api/workflows/templates');
		return res.items;
	}

	async function createFromTemplate(key: string, name: string): Promise<WorkflowDefinition> {
		const created = await api.post<WorkflowDefinition>('/api/workflows/from-template', {
			template_key: key,
			name,
		});
		workflows = [...workflows, created];
		total += 1;
		return created;
	}

	async function listVersions(id: string): Promise<WorkflowVersion[]> {
		const res = await api.get<{ items: WorkflowVersion[] }>(`/api/workflows/${id}/versions`);
		return res.items;
	}

	async function createVersion(id: string, note: string | null): Promise<WorkflowVersion> {
		return api.post<WorkflowVersion>(`/api/workflows/${id}/versions`, { note });
	}

	async function restoreVersion(id: string, versionId: string): Promise<WorkflowDefinition> {
		const updated = await api.post<WorkflowDefinition>(
			`/api/workflows/${id}/restore/${versionId}`,
			{}
		);
		workflows = workflows.map((w) => (w.id === id ? updated : w));
		return updated;
	}

	async function diffVersions(
		id: string,
		fromId: string,
		toId: string
	): Promise<WorkflowDiff> {
		return api.get<WorkflowDiff>(
			`/api/workflows/${id}/versions/diff?from=${encodeURIComponent(fromId)}&to=${encodeURIComponent(toId)}`
		);
	}

	async function simulate(
		id: string,
		payload: { invoice: SimInvoice } | { invoice_id: string }
	): Promise<SimulationResult> {
		return api.post<SimulationResult>(`/api/workflows/${id}/simulate`, payload);
	}

	async function exportDefinition(id: string): Promise<WorkflowExport> {
		return api.get<WorkflowExport>(`/api/workflows/${id}/export`);
	}

	async function importDefinition(payload: {
		name?: string | null;
		definition: WorkflowExport;
	}): Promise<WorkflowDefinition> {
		const created = await api.post<WorkflowDefinition>('/api/workflows/import', payload);
		workflows = [...workflows, created];
		total += 1;
		return created;
	}

	async function fetchActiveSteps() {
		try {
			const data = await api.get<Record<string, unknown>>('/api/workflows/active/steps');
			activeSteps = {
				extraction: (data.extraction as boolean) ?? false,
				approval: (data.approval as boolean) ?? false,
				erp_export: (data.erp_export as boolean) ?? false,
				approval_config: (data.approval_config as ApprovalConfig) ?? null,
			};
		} catch {
			activeSteps = { ...DEFAULT_ACTIVE_STEPS };
		}
	}

	return {
		get all() {
			return workflows;
		},
		get loading() {
			return loading;
		},
		get total() {
			return total;
		},
		get hasMore() {
			return workflows.length < total;
		},
		get activeSteps() {
			return activeSteps;
		},
		fetch,
		loadMore,
		fetchActiveSteps,
		getById,
		create,
		update,
		remove,
		bulkRemove,
		listTemplates,
		createFromTemplate,
		listVersions,
		createVersion,
		restoreVersion,
		diffVersions,
		simulate,
		exportDefinition,
		importDefinition,
	};
}

export const workflowStore = createWorkflowStore();
