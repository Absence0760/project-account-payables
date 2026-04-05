import type { WorkflowDefinition, WorkflowStep } from '$lib/types/workflow';
import { api } from '$lib/api';

function createWorkflowStore() {
	let workflows = $state<WorkflowDefinition[]>([]);
	let loading = $state(false);

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

	return {
		get all() {
			return workflows;
		},
		get loading() {
			return loading;
		},
		fetch,
		getById,
		create,
		update,
		remove,
	};
}

export const workflowStore = createWorkflowStore();
