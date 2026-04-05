<script lang="ts">
	import { page } from '$app/stores';
	import { workflowStore } from '$lib/stores/workflows.svelte';
	import { adminStore } from '$lib/stores/admin.svelte';
	import { toast } from '$lib/components/Toast.svelte';
	import type {
		WorkflowDefinition,
		WorkflowStep,
		WorkflowStepType,
		ExtractionStepConfig,
		ApprovalStepConfig,
		ErpExportStepConfig,
		ErpExportFormat,
	} from '$lib/types/workflow';
	import {
		STEP_TYPE_LABELS,
		STEP_TYPE_DESCRIPTIONS,
		ERP_FORMAT_LABELS,
		DEFAULT_EXTRACTION_CONFIG,
		DEFAULT_APPROVAL_CONFIG,
		DEFAULT_ERP_CONFIG,
	} from '$lib/types/workflow';

	let workflow = $state<WorkflowDefinition | null>(null);
	let steps = $state<WorkflowStep[]>([]);
	let selectedIndex = $state<number>(0);
	let saving = $state(false);
	let dirty = $state(false);
	let editingName = $state(false);
	let nameInput = $state('');
	let descInput = $state('');

	const id = $derived($page.params.id ?? '');

	$effect(() => {
		if (id) loadWorkflow(id);
		adminStore.fetchUsers();
	});

	async function loadWorkflow(wfId: string) {
		try {
			const wf = await workflowStore.getById(wfId);
			workflow = wf;
			steps = structuredClone(wf.steps_config?.steps ?? []);
			nameInput = wf.name;
			descInput = wf.description ?? '';
			selectedIndex = 0;
		} catch {
			toast('Workflow not found', 'error');
		}
	}

	let selectedStep = $derived(steps[selectedIndex] ?? null);

	function addStep(type: WorkflowStepType) {
		const defaults: Record<WorkflowStepType, { name: string; config: object }> = {
			extraction: { name: 'Data Extraction', config: { ...DEFAULT_EXTRACTION_CONFIG } },
			approval: { name: 'Approval', config: { ...DEFAULT_APPROVAL_CONFIG } },
			erp_export: { name: 'ERP Export', config: { ...DEFAULT_ERP_CONFIG } },
		};
		const d = defaults[type];
		const newStep: WorkflowStep = {
			number: steps.length + 1,
			type,
			name: d.name,
			enabled: true,
			config: d.config as ExtractionStepConfig | ApprovalStepConfig | ErpExportStepConfig,
		};
		steps = [...steps, newStep];
		selectedIndex = steps.length - 1;
		dirty = true;
	}

	function removeStep(index: number) {
		if (steps.length <= 1) return;
		steps = steps.filter((_, i) => i !== index).map((s, i) => ({ ...s, number: i + 1 }));
		if (selectedIndex >= steps.length) selectedIndex = steps.length - 1;
		dirty = true;
	}

	function moveStep(index: number, direction: -1 | 1) {
		const target = index + direction;
		if (target < 0 || target >= steps.length) return;
		const arr = [...steps];
		[arr[index], arr[target]] = [arr[target], arr[index]];
		steps = arr.map((s, i) => ({ ...s, number: i + 1 }));
		selectedIndex = target;
		dirty = true;
	}

	function updateStepField(index: number, field: string, value: unknown) {
		steps = steps.map((s, i) => (i === index ? { ...s, [field]: value } : s));
		dirty = true;
	}

	function updateStepConfig(index: number, key: string, value: unknown) {
		steps = steps.map((s, i) =>
			i === index ? { ...s, config: { ...s.config, [key]: value } } : s
		);
		dirty = true;
	}

	async function handleSave() {
		if (!workflow) return;
		saving = true;
		try {
			await workflowStore.update(workflow.id, {
				name: nameInput.trim(),
				description: descInput.trim() || undefined,
				steps,
			});
			dirty = false;
			toast('Workflow saved', 'success');
		} catch (e: unknown) {
			toast(e instanceof Error ? e.message : 'Failed to save', 'error');
		} finally {
			saving = false;
		}
	}

	async function toggleActive() {
		if (!workflow) return;
		try {
			const updated = await workflowStore.update(workflow.id, {
				is_active: !workflow.is_active,
			});
			workflow = updated;
			toast(updated.is_active ? 'Workflow activated' : 'Workflow deactivated', 'success');
		} catch (e: unknown) {
			toast(e instanceof Error ? e.message : 'Failed to update', 'error');
		}
	}

	function stepIcon(type: WorkflowStepType): string {
		const icons: Record<WorkflowStepType, string> = {
			extraction: 'M9 2L4.5 6.5 9 11M15 2l4.5 4.5L15 11M12 2v9',
			approval: 'M9 12l2 2 4-4m5 2a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
			erp_export: 'M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2M7 10l5 5 5-5M12 15V3',
		};
		return icons[type];
	}
</script>

<div class="workspace">
	{#if !workflow}
		<div class="loading">Loading...</div>
	{:else}
		<header class="toolbar">
			<div class="toolbar-left">
				<a href="/workflows" class="back-link">
					<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg>
				</a>
				{#if editingName}
					<input
						class="name-input"
						type="text"
						bind:value={nameInput}
						onblur={() => { editingName = false; dirty = true; }}
						onkeydown={(e) => { if (e.key === 'Enter') { editingName = false; dirty = true; } }}
					/>
				{:else}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<h2 class="page-title" onclick={() => (editingName = true)} onkeydown={() => {}}>
						{nameInput}
						{#if workflow.is_default}
							<span class="default-badge">Default</span>
						{/if}
					</h2>
				{/if}
			</div>
			<div class="toolbar-right">
				<button class="btn-toggle" class:active={workflow.is_active} onclick={toggleActive}>
					{workflow.is_active ? 'Active' : 'Inactive'}
				</button>
				<button class="btn-save" disabled={saving || !dirty} onclick={handleSave}>
					{saving ? 'Saving...' : 'Save'}
				</button>
			</div>
		</header>

		<div class="description-row">
			<input
				class="desc-input"
				type="text"
				placeholder="Add a description..."
				bind:value={descInput}
				oninput={() => (dirty = true)}
			/>
		</div>

		<div class="editor">
			<!-- Left: step list / pipeline -->
			<div class="pipeline">
				<div class="pipeline-header">
					<span class="pipeline-label">Pipeline</span>
				</div>

				<div class="step-list">
					{#each steps as step, i (i)}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div
							class="step-card"
							class:selected={selectedIndex === i}
							class:disabled={!step.enabled}
							onclick={() => (selectedIndex = i)}
							onkeydown={() => {}}
						>
							<div class="step-header">
								<svg class="step-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
									<path d={stepIcon(step.type)} />
								</svg>
								<div class="step-info">
									<div class="step-name">{step.name}</div>
									<div class="step-type">{STEP_TYPE_LABELS[step.type]}</div>
								</div>
								<div class="step-number">{step.number}</div>
							</div>
						</div>
						{#if i < steps.length - 1}
							<div class="connector">
								<svg width="2" height="20" viewBox="0 0 2 20">
									<line x1="1" y1="0" x2="1" y2="20" stroke="var(--border)" stroke-width="2" stroke-dasharray="4 3" />
								</svg>
							</div>
						{/if}
					{/each}
				</div>

				<div class="add-step">
					<span class="add-label">Add step</span>
					<div class="add-buttons">
						<button class="add-btn" onclick={() => addStep('extraction')}>Extraction</button>
						<button class="add-btn" onclick={() => addStep('approval')}>Approval</button>
						<button class="add-btn" onclick={() => addStep('erp_export')}>ERP Export</button>
					</div>
				</div>
			</div>

			<!-- Right: step config panel -->
			<div class="config-panel">
				{#if selectedStep}
					<div class="config-header">
						<h3>{selectedStep.name}</h3>
						<p class="config-desc">{STEP_TYPE_DESCRIPTIONS[selectedStep.type]}</p>
					</div>

					<div class="config-body">
						<!-- Common fields -->
						<div class="field">
							<label for="step-name">Step Name</label>
							<input
								id="step-name"
								type="text"
								value={selectedStep.name}
								oninput={(e) => updateStepField(selectedIndex, 'name', e.currentTarget.value)}
							/>
						</div>

						<div class="field toggle-field">
							<label for="step-enabled">Enabled</label>
							<button
								id="step-enabled"
								class="toggle"
								class:on={selectedStep.enabled}
								onclick={() => updateStepField(selectedIndex, 'enabled', !selectedStep.enabled)}
							>
								<span class="toggle-knob"></span>
							</button>
						</div>

						<hr />

						<!-- Extraction config -->
						{#if selectedStep.type === 'extraction'}
							{@const cfg = selectedStep.config as ExtractionStepConfig}
							<div class="field toggle-field">
								<label for="auto-approve">Auto-approve on high confidence</label>
								<button
									id="auto-approve"
									class="toggle"
									class:on={cfg.auto_approve_enabled}
									onclick={() => updateStepConfig(selectedIndex, 'auto_approve_enabled', !cfg.auto_approve_enabled)}
								>
									<span class="toggle-knob"></span>
								</button>
							</div>

							{#if cfg.auto_approve_enabled}
								<div class="field">
									<label for="threshold">Confidence Threshold</label>
									<div class="range-row">
										<input
											id="threshold"
											type="range"
											min="0.5"
											max="1"
											step="0.01"
											value={cfg.auto_approve_threshold}
											oninput={(e) => updateStepConfig(selectedIndex, 'auto_approve_threshold', parseFloat(e.currentTarget.value))}
										/>
										<span class="range-value">{Math.round(cfg.auto_approve_threshold * 100)}%</span>
									</div>
									<p class="field-hint">
										Invoices with extraction confidence above this threshold will skip manual approval.
									</p>
								</div>
							{/if}
						{/if}

						<!-- Approval config -->
						{#if selectedStep.type === 'approval'}
							{@const cfg = selectedStep.config as ApprovalStepConfig}
							<div class="field toggle-field">
								<label for="approval-required">Approval Required</label>
								<button
									id="approval-required"
									class="toggle"
									class:on={cfg.required}
									onclick={() => updateStepConfig(selectedIndex, 'required', !cfg.required)}
								>
									<span class="toggle-knob"></span>
								</button>
							</div>

							<div class="field">
								<label for="approver-strategy">Approver Assignment</label>
								<select
									id="approver-strategy"
									value={cfg.approver_strategy}
									onchange={(e) => updateStepConfig(selectedIndex, 'approver_strategy', e.currentTarget.value)}
								>
									<option value="manual">Manual — assign per invoice</option>
									<option value="specific">Specific — always same approver</option>
									<option value="auto">Auto — skip approval step</option>
								</select>
							</div>

							{#if cfg.approver_strategy === 'specific'}
								<div class="field">
									<label>Approvers</label>
									<div class="approver-checks">
										{#each adminStore.users.filter(u => u.is_active) as user}
											<label class="approver-check">
												<input
													type="checkbox"
													checked={(cfg.approver_ids ?? []).includes(user.id)}
													onchange={() => {
														const ids = cfg.approver_ids ?? [];
														const next = ids.includes(user.id)
															? ids.filter((id: string) => id !== user.id)
															: [...ids, user.id];
														updateStepConfig(selectedIndex, 'approver_ids', next);
													}}
												/>
												<span class="approver-name">{user.full_name}</span>
												<span class="approver-email">{user.email}</span>
											</label>
										{/each}
									</div>
									{#if (cfg.approver_ids ?? []).length > 0}
										<p class="field-hint">Invoices will be round-robin assigned to the {cfg.approver_ids.length} selected approver{cfg.approver_ids.length > 1 ? 's' : ''}.</p>
									{:else}
										<p class="field-hint warning">No approvers selected. Select at least one user.</p>
									{/if}
								</div>
							{/if}

							{#if cfg.approver_strategy === 'auto'}
								<p class="field-hint warning">
									Invoices will be automatically approved without human review. Use with caution.
								</p>
							{/if}
						{/if}

						<!-- ERP Export config -->
						{#if selectedStep.type === 'erp_export'}
							{@const cfg = selectedStep.config as ErpExportStepConfig}
							<div class="field">
								<label for="erp-system">ERP System</label>
								<input
									id="erp-system"
									type="text"
									placeholder="e.g. SAP, NetSuite, QuickBooks..."
									value={cfg.erp_system}
									oninput={(e) => updateStepConfig(selectedIndex, 'erp_system', e.currentTarget.value)}
								/>
							</div>

							<div class="field">
								<label for="export-format">Export Format</label>
								<select
									id="export-format"
									value={cfg.export_format}
									onchange={(e) => updateStepConfig(selectedIndex, 'export_format', e.currentTarget.value)}
								>
									{#each Object.entries(ERP_FORMAT_LABELS) as [value, label]}
										<option {value}>{label}</option>
									{/each}
								</select>
								<p class="field-hint">
									{#if cfg.export_format === 'xml'}
										Generates an XML document compatible with most ERP systems.
									{:else if cfg.export_format === 'csv'}
										Flat CSV file for import into spreadsheet-based workflows.
									{:else if cfg.export_format === 'cxml'}
										Commerce XML for procurement systems (Ariba, Coupa).
									{:else if cfg.export_format === 'edi'}
										EDI 810 format for legacy ERP integrations.
									{:else}
										Standard JSON payload sent via API.
									{/if}
								</p>
							</div>

							<div class="field">
								<label for="endpoint-url">Endpoint URL</label>
								<input
									id="endpoint-url"
									type="url"
									placeholder="https://erp.example.com/api/invoices"
									value={cfg.endpoint_url}
									oninput={(e) => updateStepConfig(selectedIndex, 'endpoint_url', e.currentTarget.value)}
								/>
								<p class="field-hint">
									Leave blank to generate a file for manual upload.
								</p>
							</div>
						{/if}
					</div>

					<div class="config-footer">
						<div class="move-btns">
							<button class="move-btn" disabled={selectedIndex === 0} onclick={() => moveStep(selectedIndex, -1)}>Move Up</button>
							<button class="move-btn" disabled={selectedIndex === steps.length - 1} onclick={() => moveStep(selectedIndex, 1)}>Move Down</button>
						</div>
						{#if steps.length > 1}
							<button class="remove-btn" onclick={() => removeStep(selectedIndex)}>Remove Step</button>
						{/if}
					</div>
				{:else}
					<div class="no-selection">Select a step to configure.</div>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	.workspace {
		max-width: 1280px;
		margin: 0 auto;
		padding: 24px 20px;
		display: flex;
		flex-direction: column;
		gap: 14px;
		min-height: 100vh;
	}

	.loading {
		padding: 60px 20px;
		text-align: center;
		color: var(--text-muted);
	}

	/* Toolbar */
	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
	}

	.toolbar-left {
		display: flex;
		align-items: center;
		gap: 10px;
	}

	.toolbar-right {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.back-link {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		border-radius: 6px;
		color: var(--text-muted);
		text-decoration: none;
		transition: all 0.15s;
	}

	.back-link:hover {
		background: var(--surface);
		color: var(--text);
	}

	.page-title {
		font-size: 1.15rem;
		font-weight: 600;
		color: var(--text);
		margin: 0;
		cursor: pointer;
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.name-input {
		font-size: 1.15rem;
		font-weight: 600;
		color: var(--text);
		background: var(--bg);
		border: 1px solid var(--accent);
		border-radius: 6px;
		padding: 4px 10px;
		outline: none;
		font-family: inherit;
	}

	.default-badge {
		font-size: 0.68rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		padding: 2px 7px;
		border-radius: 4px;
		background: rgba(99, 140, 255, 0.15);
		color: var(--accent);
	}

	.btn-toggle {
		padding: 6px 14px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-toggle.active {
		border-color: #1fa86a;
		color: #1fa86a;
	}

	.btn-save {
		padding: 8px 18px;
		border-radius: 6px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-save:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	/* Description */
	.description-row {
		padding: 0;
	}

	.desc-input {
		width: 100%;
		padding: 8px 12px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.85rem;
		font-family: inherit;
		outline: none;
		box-sizing: border-box;
	}

	.desc-input:focus {
		border-color: var(--accent);
		color: var(--text);
	}

	/* Messages */
	.error-bar {
		padding: 10px 14px;
		border-radius: 6px;
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
		font-size: 0.85rem;
	}

	.success-bar {
		padding: 10px 14px;
		border-radius: 6px;
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
		font-size: 0.85rem;
	}

	/* Editor layout */
	.editor {
		display: grid;
		grid-template-columns: 300px 1fr;
		gap: 16px;
		min-height: 500px;
	}

	/* Pipeline (left) */
	.pipeline {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		display: flex;
		flex-direction: column;
	}

	.pipeline-header {
		padding: 12px 14px;
		border-bottom: 1px solid var(--border);
	}

	.pipeline-label {
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.step-list {
		padding: 14px;
		display: flex;
		flex-direction: column;
		align-items: center;
		flex: 1;
	}

	.step-card {
		width: 100%;
		padding: 10px 12px;
		border: 1px solid var(--border);
		border-radius: 8px;
		cursor: pointer;
		transition: all 0.15s;
		background: var(--bg);
	}

	.step-card:hover {
		border-color: var(--accent);
	}

	.step-card.selected {
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.2);
	}

	.step-card.disabled {
		opacity: 0.45;
	}

	.step-header {
		display: flex;
		align-items: center;
		gap: 10px;
	}

	.step-icon {
		color: var(--accent);
		flex-shrink: 0;
	}

	.step-info {
		flex: 1;
		min-width: 0;
	}

	.step-name {
		font-size: 0.88rem;
		font-weight: 500;
		color: var(--text);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.step-type {
		font-size: 0.74rem;
		color: var(--text-muted);
	}

	.step-number {
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

	.connector {
		display: flex;
		justify-content: center;
		padding: 2px 0;
	}

	.add-step {
		padding: 14px;
		border-top: 1px solid var(--border);
	}

	.add-label {
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		display: block;
		margin-bottom: 8px;
	}

	.add-buttons {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}

	.add-btn {
		padding: 5px 12px;
		border-radius: 5px;
		border: 1px dashed var(--border);
		background: none;
		color: var(--text-muted);
		font-size: 0.78rem;
		cursor: pointer;
		font-family: inherit;
		transition: all 0.15s;
	}

	.add-btn:hover {
		border-color: var(--accent);
		color: var(--accent);
		border-style: solid;
	}

	/* Config panel (right) */
	.config-panel {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		display: flex;
		flex-direction: column;
	}

	.config-header {
		padding: 16px 20px;
		border-bottom: 1px solid var(--border);
	}

	.config-header h3 {
		margin: 0;
		font-size: 1rem;
		color: var(--text);
	}

	.config-desc {
		margin: 4px 0 0;
		font-size: 0.82rem;
		color: var(--text-muted);
	}

	.config-body {
		padding: 20px;
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.config-body hr {
		border: none;
		border-top: 1px solid var(--border);
		margin: 4px 0;
	}

	.field label {
		display: block;
		font-size: 0.78rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		margin-bottom: 5px;
	}

	.field input[type='text'],
	.field input[type='url'],
	.field select {
		width: 100%;
		padding: 8px 10px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.88rem;
		outline: none;
		box-sizing: border-box;
	}

	.field input:focus,
	.field select:focus {
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.field select {
		cursor: pointer;
	}

	.field-hint {
		margin: 5px 0 0;
		font-size: 0.78rem;
		color: var(--text-muted);
		line-height: 1.4;
	}

	.field-hint.warning {
		color: #d4940a;
	}

	.approver-checks {
		display: flex;
		flex-direction: column;
		gap: 6px;
		max-height: 200px;
		overflow-y: auto;
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 8px 10px;
		background: var(--bg);
	}

	.approver-check {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 0.85rem;
		cursor: pointer;
		padding: 4px 0;
	}

	.approver-check input[type='checkbox'] {
		accent-color: var(--accent);
		cursor: pointer;
		flex-shrink: 0;
	}

	.approver-name {
		font-weight: 500;
		color: var(--text);
	}

	.approver-email {
		color: var(--text-muted);
		font-size: 0.78rem;
	}

	/* Toggle switch */
	.toggle-field {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.toggle-field label {
		margin-bottom: 0;
	}

	.toggle {
		position: relative;
		width: 40px;
		height: 22px;
		border-radius: 12px;
		border: none;
		background: var(--border);
		cursor: pointer;
		padding: 0;
		transition: background 0.2s;
	}

	.toggle.on {
		background: var(--accent);
	}

	.toggle-knob {
		position: absolute;
		top: 3px;
		left: 3px;
		width: 16px;
		height: 16px;
		border-radius: 50%;
		background: #fff;
		transition: transform 0.2s;
	}

	.toggle.on .toggle-knob {
		transform: translateX(18px);
	}

	/* Range slider */
	.range-row {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.range-row input[type='range'] {
		flex: 1;
		accent-color: var(--accent);
	}

	.range-value {
		font-size: 0.88rem;
		font-weight: 600;
		color: var(--accent);
		min-width: 42px;
		text-align: right;
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
	}

	/* Config footer */
	.config-footer {
		padding: 14px 20px;
		border-top: 1px solid var(--border);
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.move-btns {
		display: flex;
		gap: 6px;
	}

	.move-btn {
		padding: 5px 12px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.move-btn:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}

	.move-btn:disabled {
		opacity: 0.35;
		cursor: not-allowed;
	}

	.remove-btn {
		padding: 5px 12px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.remove-btn:hover {
		border-color: #e04040;
		color: #e04040;
	}

	.no-selection {
		padding: 40px 20px;
		text-align: center;
		color: var(--text-muted);
	}
</style>
