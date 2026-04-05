import type { WorkflowDefinition, WorkflowStep } from '$lib/types/workflow';
import { api } from '$lib/api';

export interface ActiveSteps {
	extraction: boolean;
	approval: boolean;
	erp_export: boolean;
}

const DEFAULT_ACTIVE_STEPS: ActiveSteps = {
	extraction: false,
	approval: false,
	erp_export: false,
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

	async function fetchActiveSteps() {
		try {
			const data = await api.get<Record<string, boolean>>('/api/workflows/active/steps');
			activeSteps = {
				extraction: data.extraction ?? false,
				approval: data.approval ?? false,
				erp_export: data.erp_export ?? false,
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
	};
}

export const workflowStore = createWorkflowStore();
