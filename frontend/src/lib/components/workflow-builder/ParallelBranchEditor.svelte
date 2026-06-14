<script lang="ts">
	import type { ParallelStepConfig, ParallelBranchConfig } from '$lib/types/workflow';
	import type { AdminUser } from '$lib/types/admin';

	type Props = {
		config: ParallelStepConfig;
		users: AdminUser[];
		onchange: (config: ParallelStepConfig) => void;
	};

	let { config, users, onchange }: Props = $props();

	let activeUsers = $derived(users.filter((u) => u.is_active));

	function patch(p: Partial<ParallelStepConfig>) {
		onchange({ ...config, ...p });
	}

	function patchBranch(idx: number, p: Partial<ParallelBranchConfig>) {
		patch({ branches: config.branches.map((b, i) => (i === idx ? { ...b, ...p } : b)) });
	}

	function addBranch() {
		const branch: ParallelBranchConfig = {
			name: `Branch ${config.branches.length + 1}`,
			approver_ids: [],
		};
		patch({ branches: [...config.branches, branch] });
	}

	function removeBranch(idx: number) {
		patch({ branches: config.branches.filter((_, i) => i !== idx) });
	}

	function toggleApprover(branchIdx: number, userId: string) {
		const current = config.branches[branchIdx].approver_ids;
		const next = current.includes(userId)
			? current.filter((id) => id !== userId)
			: [...current, userId];
		patchBranch(branchIdx, { approver_ids: next });
	}
</script>

<div class="parallel">
	{#each config.branches as branch, idx (idx)}
		<div class="branch-card">
			<div class="branch-header">
				<input
					class="branch-name"
					type="text"
					placeholder="Branch name"
					value={branch.name}
					oninput={(e) => patchBranch(idx, { name: e.currentTarget.value })}
				/>
				<button
					type="button"
					class="icon-btn danger"
					title="Remove branch"
					aria-label="Remove branch"
					disabled={config.branches.length <= 1}
					onclick={() => removeBranch(idx)}
				>×</button>
			</div>
			<span class="field-label">Approvers</span>
			<div class="user-chips">
				{#each activeUsers as u (u.id)}
					<button
						type="button"
						class="chip"
						class:on={branch.approver_ids.includes(u.id)}
						onclick={() => toggleApprover(idx, u.id)}
					>
						{u.full_name}
					</button>
				{/each}
				{#if activeUsers.length === 0}
					<p class="hint">No active users to assign.</p>
				{/if}
			</div>
		</div>
	{/each}

	<button type="button" class="add-branch-btn" onclick={addBranch}>+ Add branch</button>

	<div class="join-row">
		<label class="join-field">
			<span class="field-label">Join</span>
			<select
				value={config.join}
				onchange={(e) => patch({ join: e.currentTarget.value as 'all' | 'any' })}
			>
				<option value="all">All branches must approve</option>
				<option value="any">Any branch satisfies (count below)</option>
			</select>
		</label>
		{#if config.join === 'any'}
			<label class="join-field">
				<span class="field-label">Min approvals</span>
				<input
					type="number"
					min="1"
					placeholder="1"
					value={config.min_approvals ?? ''}
					oninput={(e) =>
						patch({
							min_approvals: e.currentTarget.value
								? Math.max(1, parseInt(e.currentTarget.value, 10) || 1)
								: null,
						})}
				/>
			</label>
		{/if}
	</div>
</div>

<style>
	.parallel {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}

	.branch-card {
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 14px;
		background: var(--bg);
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.branch-header {
		display: flex;
		align-items: center;
		gap: 10px;
	}

	.branch-name {
		flex: 1;
		min-width: 0;
		font-size: 0.9rem;
		font-weight: 600;
		border: 1px solid transparent;
		background: transparent;
		padding: 4px 8px;
		color: var(--text);
		border-radius: 4px;
		font-family: inherit;
	}

	.branch-name:hover {
		border-color: var(--border);
	}

	.branch-name:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.field-label {
		font-size: 0.72rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.user-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.chip {
		padding: 4px 10px;
		border: 1px solid var(--border);
		border-radius: 999px;
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.78rem;
		cursor: pointer;
		font-family: inherit;
	}

	.chip:hover:not(.on) {
		border-color: var(--accent);
		color: var(--accent);
	}

	.chip.on {
		background: var(--accent);
		color: white;
		border-color: var(--accent);
	}

	.icon-btn {
		width: 28px;
		height: 28px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--surface);
		color: var(--text-muted);
		cursor: pointer;
		font-size: 14px;
		font-family: inherit;
	}

	.icon-btn.danger:hover:not(:disabled) {
		border-color: #e04040;
		color: #e04040;
	}

	.icon-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.add-branch-btn {
		padding: 9px 14px;
		border: 1px dashed var(--border);
		border-radius: 8px;
		background: transparent;
		cursor: pointer;
		font-size: 0.85rem;
		color: var(--text-muted);
		font-family: inherit;
		align-self: flex-start;
	}

	.add-branch-btn:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.join-row {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 12px;
	}

	.join-field {
		display: flex;
		flex-direction: column;
		gap: 5px;
	}

	.join-field select,
	.join-field input {
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 6px;
		font-size: 0.85rem;
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		box-sizing: border-box;
		width: 100%;
	}

	.join-field select:focus,
	.join-field input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.hint {
		font-size: 0.78rem;
		color: var(--text-muted);
		margin: 0;
	}
</style>
