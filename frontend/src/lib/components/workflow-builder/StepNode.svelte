<script lang="ts">
	import type { WorkflowStep } from '$lib/types/workflow';
	import { STEP_TYPE_LABELS } from '$lib/types/workflow';

	type Props = {
		step: WorkflowStep;
		index: number;
		selected: boolean;
		ondragstart: (e: DragEvent) => void;
		ondragend: () => void;
		onselect: () => void;
		ontoggle: () => void;
		ondelete: () => void;
	};

	let { step, index, selected, ondragstart, ondragend, onselect, ontoggle, ondelete }: Props =
		$props();

	const ICONS: Record<string, string> = {
		extraction: 'M9 2L4.5 6.5 9 11M15 2l4.5 4.5L15 11M12 2v9',
		approval: 'M9 12l2 2 4-4m5 2a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
		erp_export: 'M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2M7 10l5 5 5-5M12 15V3',
		condition: 'M12 2v6m0 0L7 13m5-5l5 5M4 17a3 3 0 1 0 6 0 3 3 0 0 0-6 0zm10 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0z',
		parallel: 'M4 6h4M4 12h4M4 18h4M14 6h6M14 12h6M14 18h6M8 12h6',
		webhook: 'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1',
		email: 'M4 4h16v16H4zM4 6l8 6 8-6',
		delay: 'M12 6v6l4 2m5-2a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
	};

	function iconPath(type: string): string {
		return ICONS[type] ?? ICONS.extraction;
	}
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="node"
	class:selected
	class:disabled={!step.enabled}
	draggable="true"
	ondragstart={ondragstart}
	ondragend={ondragend}
	onclick={onselect}
	onkeydown={(e) => {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			onselect();
		}
	}}
	role="button"
	tabindex="0"
	data-step-number={step.number}
>
	<span class="drag-handle" title="Drag to reorder" aria-hidden="true">
		<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
			<circle cx="9" cy="6" r="1.6" /><circle cx="15" cy="6" r="1.6" />
			<circle cx="9" cy="12" r="1.6" /><circle cx="15" cy="12" r="1.6" />
			<circle cx="9" cy="18" r="1.6" /><circle cx="15" cy="18" r="1.6" />
		</svg>
	</span>

	<svg
		class="node-icon"
		width="18"
		height="18"
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		aria-hidden="true"
	>
		<path d={iconPath(step.type)} />
	</svg>

	<div class="node-info">
		<div class="node-name">{step.name}</div>
		<div class="node-type">{STEP_TYPE_LABELS[step.type]}</div>
	</div>

	<div class="node-actions">
		<button
			type="button"
			class="enabled-toggle"
			class:on={step.enabled}
			title={step.enabled ? 'Enabled' : 'Disabled'}
			aria-label={step.enabled ? 'Disable step' : 'Enable step'}
			aria-pressed={step.enabled}
			onclick={(e) => {
				e.stopPropagation();
				ontoggle();
			}}
		>
			<span class="knob"></span>
		</button>
		<button
			type="button"
			class="icon-action danger"
			title="Delete step"
			aria-label="Delete step"
			onclick={(e) => {
				e.stopPropagation();
				ondelete();
			}}
		>×</button>
	</div>

	<div class="node-number">{step.number}</div>
</div>

<style>
	.node {
		position: relative;
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 10px 12px;
		border: 1px solid var(--border);
		border-radius: 8px;
		background: var(--bg);
		cursor: pointer;
		transition:
			border-color 0.15s,
			box-shadow 0.15s;
		box-sizing: border-box;
	}

	.node:hover {
		border-color: var(--accent);
	}

	.node.selected {
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.2);
	}

	.node.disabled {
		opacity: 0.5;
	}

	.drag-handle {
		display: inline-flex;
		align-items: center;
		color: var(--text-muted);
		cursor: grab;
		flex-shrink: 0;
	}

	.drag-handle:active {
		cursor: grabbing;
	}

	.node-icon {
		color: var(--accent);
		flex-shrink: 0;
	}

	.node-info {
		flex: 1;
		min-width: 0;
	}

	.node-name {
		font-size: 0.88rem;
		font-weight: 500;
		color: var(--text);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.node-type {
		font-size: 0.74rem;
		color: var(--text-muted);
	}

	.node-actions {
		display: flex;
		align-items: center;
		gap: 6px;
		flex-shrink: 0;
	}

	.enabled-toggle {
		position: relative;
		width: 30px;
		height: 17px;
		border-radius: 9px;
		border: none;
		background: var(--border);
		cursor: pointer;
		padding: 0;
		transition: background 0.2s;
	}

	.enabled-toggle.on {
		background: var(--accent);
	}

	.knob {
		position: absolute;
		top: 2px;
		left: 2px;
		width: 13px;
		height: 13px;
		border-radius: 50%;
		background: #fff;
		transition: transform 0.2s;
	}

	.enabled-toggle.on .knob {
		transform: translateX(13px);
	}

	.icon-action {
		width: 22px;
		height: 22px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--surface);
		color: var(--text-muted);
		cursor: pointer;
		font-size: 14px;
		line-height: 1;
		font-family: inherit;
	}

	.icon-action.danger:hover {
		border-color: #e04040;
		color: #e04040;
	}

	.node-number {
		width: 22px;
		height: 22px;
		border-radius: 50%;
		background: var(--surface);
		border: 1px solid var(--border);
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--text-muted);
		display: grid;
		place-items: center;
		flex-shrink: 0;
	}
</style>
