import type { WorkflowDefinition, WorkflowStep } from '$lib/types/workflow';
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

function createWorkflowStore() {
	let workflows = $state<WorkflowDefinition[]>([]);
	let loading = $state(false);
	let activeSteps = $state<ActiveSteps>({ ...DEFAULT_ACTIVE_STEPS });

	async function fetch() {
		loading = true;
		try {
			workflows = await api.get<WorkflowDefinition[]>('/api/workflows');
		} finally {
			loading = false;
		}
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
		return result;
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
		get activeSteps() {
			return activeSteps;
		},
		fetch,
		fetchActiveSteps,
		getById,
		create,
		update,
		remove,
		bulkRemove,
	};
}

export const workflowStore = createWorkflowStore();
