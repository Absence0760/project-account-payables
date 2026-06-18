<script lang="ts">
	import { page } from '$app/stores';
	import { workflowStore } from '$lib/stores/workflows.svelte';
	import { adminStore } from '$lib/stores/admin.svelte';
	import { api } from '$lib/api';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import ApprovalMatrixEditor from '$lib/components/modals/ApprovalMatrixEditor.svelte';
	import WorkflowCanvas from '$lib/components/workflow-builder/WorkflowCanvas.svelte';
	import StepPalette from '$lib/components/workflow-builder/StepPalette.svelte';
	import ConditionBuilder from '$lib/components/workflow-builder/ConditionBuilder.svelte';
	import ParallelBranchEditor from '$lib/components/workflow-builder/ParallelBranchEditor.svelte';
	import CustomStepConfig from '$lib/components/workflow-builder/CustomStepConfig.svelte';
	import type {
		WorkflowDefinition,
		WorkflowStep,
		WorkflowStepType,
		StepConfig,
		ExtractionStepConfig,
		ApprovalStepConfig,
		ApprovalLevelConfig,
		ErpExportStepConfig,
		ConditionStepConfig,
		ParallelStepConfig,
		WebhookStepConfig,
		EmailStepConfig,
		DelayStepConfig,
	} from '$lib/types/workflow';
	import {
		STEP_TYPE_LABELS,
		STEP_TYPE_DESCRIPTIONS,
		ERP_FORMAT_LABELS,
		DEFAULT_STEP_CONFIGS,
	} from '$lib/types/workflow';

	let workflow = $state<WorkflowDefinition | null>(null);
	let steps = $state<WorkflowStep[]>([]);
	let selectedIndex = $state<number>(0);
	let saving = $state(false);
	let dirty = $state(false);
	let editingName = $state(false);
	let nameInput = $state('');
	let descInput = $state('');
	let approverSearch = $state('');
	let approverDropdownOpen = $state(false);
	let erpMethod = $state<string>('merge_dev');
	// Set while a palette item is being dragged, so the canvas can show drop slots.
	let paletteDragType = $state<WorkflowStepType | null>(null);

	const id = $derived($page.params.id ?? '');

	$effect(() => {
		if (id) loadWorkflow(id);
		adminStore.fetchUsers();
		loadErpMethod();
	});

	async function loadErpMethod() {
		try {
			const org = await api.get<{ settings: { erp?: { integration_method?: string } } }>(
				'/api/organization'
			);
			erpMethod = org.settings?.erp?.integration_method ?? 'merge_dev';
		} catch {
			// default to merge_dev
		}
	}

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

	function renumber(arr: WorkflowStep[]): WorkflowStep[] {
		return arr.map((s, i) => ({ ...s, number: i + 1 }));
	}

	function makeStep(type: WorkflowStepType): WorkflowStep {
		return {
			number: steps.length + 1,
			type,
			name: STEP_TYPE_LABELS[type],
			enabled: true,
			config: DEFAULT_STEP_CONFIGS[type](),
		};
	}

	function addStep(type: WorkflowStepType) {
		const next = renumber([...steps, makeStep(type)]);
		steps = next;
		selectedIndex = next.length - 1;
		dirty = true;
	}

	function addStepAt(type: WorkflowStepType, index: number) {
		const clamped = Math.max(0, Math.min(index, steps.length));
		const arr = [...steps];
		arr.splice(clamped, 0, makeStep(type));
		steps = renumber(arr);
		selectedIndex = clamped;
		dirty = true;
	}

	function reorderStep(from: number, to: number) {
		if (from === to) return;
		const arr = [...steps];
		const [moved] = arr.splice(from, 1);
		arr.splice(to, 0, moved);
		steps = renumber(arr);
		selectedIndex = to;
		dirty = true;
	}

	function removeStep(index: number) {
		if (steps.length <= 1) return;
		steps = renumber(steps.filter((_, i) => i !== index));
		if (selectedIndex >= steps.length) selectedIndex = steps.length - 1;
		dirty = true;
	}

	function toggleStep(index: number) {
		steps = steps.map((s, i) => (i === index ? { ...s, enabled: !s.enabled } : s));
		dirty = true;
	}

	function updateStepField(index: number, field: 'name' | 'enabled', value: unknown) {
		steps = steps.map((s, i) => (i === index ? { ...s, [field]: value } : s));
		dirty = true;
	}

	function updateStepConfig(index: number, key: string, value: unknown) {
		steps = steps.map((s, i) =>
			i === index ? { ...s, config: { ...s.config, [key]: value } } : s
		);
		dirty = true;
	}

	function replaceStepConfig(index: number, config: StepConfig) {
		steps = steps.map((s, i) => (i === index ? { ...s, config } : s));
		dirty = true;
	}

	function handleWindowClick(e: MouseEvent) {
		if (approverDropdownOpen && !(e.target as HTMLElement).closest('.approver-search-wrap')) {
			approverDropdownOpen = false;
		}
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
</script>

<svelte:window onclick={handleWindowClick} />

<div class="workspace">
	{#if !workflow}
		<div class="loading">Loading...</div>
	{:else}
		<header class="toolbar">
			<div class="toolbar-left">
				<a href="/workflows" class="back-link" aria-label="Back to workflows">
					<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg>
				</a>
				{#if editingName}
					<input
						class="name-input"
						type="text"
						aria-label="Workflow name"
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
			<!-- Left: draggable step library -->
			<StepPalette
				ondragtype={(type) => (paletteDragType = type)}
				ondragend={() => (paletteDragType = null)}
				onadd={addStep}
			/>

			<!-- Centre: flow canvas -->
			<div class="canvas-pane">
				<div class="pane-header">
					<span class="pane-label">Flow</span>
				</div>
				<WorkflowCanvas
					{steps}
					{selectedIndex}
					paletteType={paletteDragType}
					onselect={(i) => (selectedIndex = i)}
					onreorder={reorderStep}
					onaddat={addStepAt}
					ontoggle={toggleStep}
					ondelete={removeStep}
				/>
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
									<option value="chain">Chain — multi-level matrix</option>
									<option value="auto">Auto — skip approval step</option>
								</select>
							</div>

							{#if cfg.approver_strategy === 'specific'}
								{@const ids = cfg.approver_ids ?? []}
								{@const selectedUsers = ids.map((uid: string) => adminStore.users.find(u => u.id === uid)).filter(Boolean)}
								{@const availableUsers = adminStore.users.filter(u => u.is_active && !ids.includes(u.id))}
								{@const query = approverSearch.toLowerCase().trim()}
								{@const filteredUsers = query
									? availableUsers.filter(u =>
										u.full_name.toLowerCase().includes(query) ||
										u.email.toLowerCase().includes(query)
									)
									: availableUsers}
								<div class="field">
									<label for="approver-search-input">Approvers</label>

									{#if selectedUsers.length > 0}
										<div class="approver-chips">
											{#each selectedUsers as user}
												<span class="approver-chip">
													{user?.full_name}
													<button
														type="button"
														class="chip-remove"
														onclick={(e) => {
															e.stopPropagation();
															updateStepConfig(selectedIndex, 'approver_ids', ids.filter((uid: string) => uid !== user?.id));
														}}
													>&times;</button>
												</span>
											{/each}
										</div>
									{/if}

									<!-- svelte-ignore a11y_no_static_element_interactions -->
									<div class="approver-search-wrap" onclick={() => (approverDropdownOpen = true)}>
										<input
											id="approver-search-input"
											type="text"
											class="approver-search"
											placeholder="Search users to add..."
											bind:value={approverSearch}
											onfocus={() => (approverDropdownOpen = true)}
										/>
										{#if approverDropdownOpen && filteredUsers.length > 0}
											<div class="approver-dropdown">
												{#each filteredUsers as user}
													<button
														type="button"
														class="approver-option"
														onclick={() => {
															updateStepConfig(selectedIndex, 'approver_ids', [...ids, user.id]);
														}}
													>
														<span class="approver-name">{user.full_name}</span>
														<span class="approver-email">{user.email}</span>
													</button>
												{/each}
											</div>
										{:else if approverDropdownOpen && query && filteredUsers.length === 0}
											<div class="approver-dropdown">
												<div class="approver-empty">No matching users</div>
											</div>
										{/if}
									</div>

									{#if ids.length > 0}
										<p class="field-hint">Invoices will be round-robin assigned to the {ids.length} selected approver{ids.length > 1 ? 's' : ''}.</p>
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

							{#if cfg.approver_strategy === 'chain'}
								<div class="field">
									<label for="approval-matrix">Approval matrix</label>
									<p class="field-hint">
										Define one or more approval levels. Each level can filter by amount or
										invoice attributes (department, GL, vendor) and supports parallel approvers
										and time-based escalation.
									</p>
									<ApprovalMatrixEditor
										chain={cfg.approval_chain ?? []}
										users={adminStore.users}
										onchange={(next: ApprovalLevelConfig[]) =>
											updateStepConfig(selectedIndex, 'approval_chain', next)}
									/>
								</div>
							{/if}

							<div class="field-divider"></div>
							<h4 class="field-section-title">Approval Thresholds</h4>

							<div class="field">
								<label for="auto-approve-below">Auto-approve below ($)</label>
								<input
									id="auto-approve-below"
									type="number"
									step="0.01"
									min="0"
									placeholder="No limit"
									value={cfg.auto_approve_below ?? ''}
									oninput={(e) => updateStepConfig(selectedIndex, 'auto_approve_below', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)}
								/>
								<p class="field-hint">Invoices below this amount skip approval entirely.</p>
							</div>

							<div class="field">
								<label for="require-cfo-above">Require CFO approval above ($)</label>
								<input
									id="require-cfo-above"
									type="number"
									step="0.01"
									min="0"
									placeholder="No limit"
									value={cfg.require_cfo_above ?? ''}
									oninput={(e) => updateStepConfig(selectedIndex, 'require_cfo_above', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)}
								/>
								<p class="field-hint">Invoices above this amount require a user with the CFO role to approve.</p>
							</div>

							<div class="field">
								<label for="max-invoice-amount">Maximum invoice amount ($)</label>
								<input
									id="max-invoice-amount"
									type="number"
									step="0.01"
									min="0"
									placeholder="No limit"
									value={cfg.max_invoice_amount ?? ''}
									oninput={(e) => updateStepConfig(selectedIndex, 'max_invoice_amount', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)}
								/>
								<p class="field-hint">Invoices above this amount are rejected automatically.</p>
							</div>
						{/if}

						<!-- ERP Export config -->
						{#if selectedStep.type === 'erp_export'}
							{@const cfg = selectedStep.config as ErpExportStepConfig}

							<p class="field-hint">ERP connection credentials are configured in <a href="/organization">Organization Settings</a>. This step controls what and when to send.</p>

							<div class="field toggle-field">
								<label for="auto-send">Auto-send on approval</label>
								<button
									id="auto-send"
									class="toggle"
									class:on={cfg.auto_send_on_approval}
									onclick={() => updateStepConfig(selectedIndex, 'auto_send_on_approval', !cfg.auto_send_on_approval)}
								>
									<span class="toggle-knob"></span>
								</button>
							</div>
							{#if !cfg.auto_send_on_approval}
								<p class="field-hint warning">Invoices will not be sent to ERP automatically. Users must manually trigger "Send to ERP" from the invoice.</p>
							{/if}

							{#if erpMethod === 'direct'}
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
							{:else}
								<p class="field-hint">Export format is handled automatically by Merge.dev based on your ERP system.</p>
							{/if}

							<div class="field-divider"></div>
							<h4 class="field-section-title">Payload Options</h4>

							<div class="field toggle-field">
								<label for="include-lines">Include line items</label>
								<button
									id="include-lines"
									class="toggle"
									class:on={cfg.include_line_items}
									onclick={() => updateStepConfig(selectedIndex, 'include_line_items', !cfg.include_line_items)}
								>
									<span class="toggle-knob"></span>
								</button>
							</div>

							<div class="field toggle-field">
								<label for="include-attach">Include PDF attachment URL</label>
								<button
									id="include-attach"
									class="toggle"
									class:on={cfg.include_attachments}
									onclick={() => updateStepConfig(selectedIndex, 'include_attachments', !cfg.include_attachments)}
								>
									<span class="toggle-knob"></span>
								</button>
							</div>
						{/if}

						<!-- Condition config -->
						{#if selectedStep.type === 'condition'}
							<ConditionBuilder
								config={selectedStep.config as ConditionStepConfig}
								{steps}
								selfNumber={selectedStep.number}
								onchange={(next) => replaceStepConfig(selectedIndex, next)}
							/>
						{/if}

						<!-- Parallel config -->
						{#if selectedStep.type === 'parallel'}
							<ParallelBranchEditor
								config={selectedStep.config as ParallelStepConfig}
								users={adminStore.users}
								onchange={(next) => replaceStepConfig(selectedIndex, next)}
							/>
						{/if}

						<!-- Webhook / Email / Delay config -->
						{#if selectedStep.type === 'webhook' || selectedStep.type === 'email' || selectedStep.type === 'delay'}
							<CustomStepConfig
								type={selectedStep.type}
								config={selectedStep.config as WebhookStepConfig | EmailStepConfig | DelayStepConfig}
								onchange={(next) => replaceStepConfig(selectedIndex, next)}
							/>
						{/if}
					</div>

					<div class="config-footer">
						<div class="move-btns">
							<button class="move-btn" disabled={selectedIndex === 0} onclick={() => reorderStep(selectedIndex, selectedIndex - 1)}>Move Up</button>
							<button class="move-btn" disabled={selectedIndex === steps.length - 1} onclick={() => reorderStep(selectedIndex, selectedIndex + 1)}>Move Down</button>
						</div>
						{#if steps.length > 1}
							<button class="remove-btn" onclick={() => removeStep(selectedIndex)}>Remove Step</button>
						{/if}
					</div>
				{:else}
					<div class="no-selection">Add a step from the library, then select it to configure.</div>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */
	.loading {
		padding: 60px 20px;
		text-align: center;
		color: var(--text-muted);
	}

	/* Toolbar */
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

	/* Editor layout: palette | canvas | config */
	.editor {
		display: grid;
		grid-template-columns: 240px 1fr 360px;
		gap: 16px;
		min-height: 500px;
		align-items: start;
	}

	.canvas-pane {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		display: flex;
		flex-direction: column;
	}

	.pane-header {
		padding: 12px 14px;
		border-bottom: 1px solid var(--border);
	}

	.pane-label {
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
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
	.field input[type='number'],
	.field select {
		/* base look (border/colour/font/chevron) from the global select recipe */
		width: 100%;
		padding: 8px 30px 8px 10px;
		border-radius: 6px;
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

	.field-divider {
		height: 1px;
		background: var(--border);
		margin: 8px 0 4px;
	}

	.field-section-title {
		font-size: 0.78rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		margin: 0 0 4px;
	}

	.approver-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin-bottom: 8px;
	}

	.approver-chip {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		padding: 3px 8px 3px 10px;
		border-radius: 14px;
		background: rgba(99, 140, 255, 0.12);
		color: var(--accent);
		font-size: 0.78rem;
		font-weight: 500;
	}

	.chip-remove {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 16px;
		height: 16px;
		border: none;
		background: none;
		color: var(--accent);
		font-size: 1rem;
		line-height: 1;
		cursor: pointer;
		border-radius: 50%;
		padding: 0;
	}

	.chip-remove:hover {
		background: rgba(99, 140, 255, 0.2);
		color: var(--text);
	}

	.approver-search-wrap {
		position: relative;
	}

	.approver-search {
		width: 100%;
		box-sizing: border-box;
	}

	.approver-dropdown {
		position: absolute;
		top: 100%;
		left: 0;
		right: 0;
		max-height: 180px;
		overflow-y: auto;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 6px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
		z-index: 20;
		margin-top: 4px;
	}

	.approver-option {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		padding: 8px 12px;
		border: none;
		background: none;
		color: var(--text);
		font-size: 0.85rem;
		cursor: pointer;
		text-align: left;
		font-family: inherit;
	}

	.approver-option:hover {
		background: rgba(99, 140, 255, 0.08);
	}

	.approver-name {
		font-weight: 500;
		color: var(--text);
	}

	.approver-email {
		color: var(--text-muted);
		font-size: 0.78rem;
	}

	.approver-empty {
		padding: 10px 12px;
		font-size: 0.82rem;
		color: var(--text-muted);
		text-align: center;
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

	@media (max-width: 1100px) {
		.editor {
			grid-template-columns: 1fr;
		}
	}
</style>
