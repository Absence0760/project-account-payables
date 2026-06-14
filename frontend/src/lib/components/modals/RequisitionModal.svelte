<script lang="ts">
	import type { Requisition, RequisitionLineItemInput } from '$lib/types/requisition';
	import { REQUISITION_STATUS_LABELS } from '$lib/types/requisition';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { createRequisition, updateRequisition } from '$lib/api/requisitions';
	import type { GlAccountOption } from '$lib/api/expenses';

	let {
		requisition,
		glAccounts = [],
		onclose,
		onsaved
	}: {
		// null → create mode; a Requisition → detail/edit mode.
		requisition: Requisition | null;
		glAccounts?: GlAccountOption[];
		onclose: () => void;
		onsaved: (r: Requisition) => void;
	} = $props();

	const isCreate = $derived(requisition === null);
	const canEdit = $derived(auth.hasAnyRole('admin', 'ap_manager', 'ap_clerk'));
	const status = $derived(requisition?.status ?? 'draft');
	// Header fields + line items are only editable while the requisition is a
	// draft (or being created) — a submitted/approved requisition is locked.
	const editable = $derived(canEdit && (isCreate || status === 'draft'));

	interface LineRow {
		description: string;
		quantity: number | null;
		unit_price: number | null;
		gl_account_id: string;
		uom: string;
	}

	function toRow(li: RequisitionLineItemInput): LineRow {
		return {
			description: li.description ?? '',
			quantity: li.quantity ?? null,
			unit_price: li.unit_price ?? null,
			gl_account_id: li.gl_account_id ?? '',
			uom: li.uom ?? ''
		};
	}

	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot */
	let requisition_number = $state(requisition?.requisition_number ?? '');
	let title = $state(requisition?.title ?? '');
	let department = $state(requisition?.department ?? '');
	let needed_by = $state(requisition?.needed_by ?? '');
	let justification = $state(requisition?.justification ?? '');
	let currency = $state(requisition?.currency ?? orgCurrency.currency ?? 'USD');
	let notes = $state(requisition?.notes ?? '');
	let lines = $state<LineRow[]>(
		(requisition?.line_items ?? []).map(toRow)
	);
	/* eslint-enable svelte/state-referenced-locally */

	// Seed a create modal with one empty line so the user has somewhere to type.
	if (isCreate && lines.length === 0) {
		lines = [{ description: '', quantity: null, unit_price: null, gl_account_id: '', uom: '' }];
	}

	let saving = $state(false);

	function numOrNull(v: unknown): number | null {
		if (v === '' || v === null || v === undefined) return null;
		const n = parseFloat(String(v));
		return Number.isFinite(n) ? n : null;
	}

	function lineTotal(l: LineRow): number {
		if (l.quantity == null || l.unit_price == null) return 0;
		return l.quantity * l.unit_price;
	}

	const computedTotal = $derived(lines.reduce((sum, l) => sum + lineTotal(l), 0));

	function addLine() {
		lines = [...lines, { description: '', quantity: null, unit_price: null, gl_account_id: '', uom: '' }];
	}

	function removeLine(idx: number) {
		lines = lines.filter((_, i) => i !== idx);
	}

	function glLabel(id: string): string {
		const g = glAccounts.find((a) => a.id === id);
		return g ? `${g.code} — ${g.name}` : '—';
	}

	function handleError(err: unknown, fallback: string) {
		toast(err instanceof Error ? err.message : fallback, 'error');
	}

	async function handleSave() {
		if (!requisition_number.trim()) return;
		saving = true;
		try {
			const lineItems: RequisitionLineItemInput[] = lines
				.filter((l) => l.description.trim() || l.quantity != null || l.unit_price != null)
				.map((l, i) => ({
					line_number: i + 1,
					description: l.description.trim() || null,
					quantity: l.quantity,
					unit_price: l.unit_price,
					gl_account_id: l.gl_account_id || null,
					uom: l.uom.trim() || null
				}));
			let saved: Requisition;
			if (isCreate) {
				saved = await createRequisition({
					requisition_number: requisition_number.trim(),
					title: title.trim() || null,
					department: department.trim() || null,
					needed_by: needed_by || null,
					justification: justification.trim() || null,
					currency: currency.trim() || 'USD',
					notes: notes.trim() || null,
					line_items: lineItems
				});
			} else {
				saved = await updateRequisition(requisition!.id, {
					requisition_number: requisition_number.trim(),
					title: title.trim() || null,
					department: department.trim() || null,
					needed_by: needed_by || null,
					justification: justification.trim() || null,
					currency: currency.trim() || 'USD',
					notes: notes.trim() || null,
					line_items: lineItems
				});
			}
			toast(isCreate ? 'Requisition created' : 'Requisition saved', 'success');
			onsaved(saved);
			onclose();
		} catch (err) {
			handleError(err, isCreate ? 'Create failed' : 'Save failed');
		} finally {
			saving = false;
		}
	}

	const modalTitle = $derived(
		isCreate
			? 'New Requisition'
			: editable
				? `Edit Requisition — ${requisition!.requisition_number}`
				: `Requisition — ${requisition!.requisition_number}`
	);
	const ariaLabel = $derived(isCreate ? 'New requisition' : 'Requisition detail');
</script>

<Modal open {ariaLabel} title={modalTitle} width="lg" {onclose}>
	<form onsubmit={(e) => { e.preventDefault(); handleSave(); }}>
		{#if !isCreate}
			<div class="status-row">
				<span class="badge {status}">{REQUISITION_STATUS_LABELS[status as keyof typeof REQUISITION_STATUS_LABELS] ?? status}</span>
				{#if requisition?.converted_po_id}
					<span class="muted">→ PO created</span>
				{/if}
				{#if requisition?.rejection_reason}
					<span class="muted">Rejected: {requisition.rejection_reason}</span>
				{/if}
			</div>
		{/if}

		<div class="form-grid">
			<label>
				<span>Requisition Number <em class="required">*</em></span>
				<input type="text" bind:value={requisition_number} required disabled={!editable} />
			</label>
			<label>
				<span>Title</span>
				<input type="text" bind:value={title} disabled={!editable} />
			</label>
			<label>
				<span>Department</span>
				<input type="text" bind:value={department} disabled={!editable} />
			</label>
			<label>
				<span>Needed By</span>
				<input type="date" bind:value={needed_by} disabled={!editable} />
			</label>
			<label>
				<span>Currency</span>
				<input type="text" bind:value={currency} maxlength="3" disabled={!editable} />
			</label>
			<label class="full-width">
				<span>Justification</span>
				<textarea bind:value={justification} rows="2" disabled={!editable}></textarea>
			</label>
			<label class="full-width">
				<span>Notes</span>
				<textarea bind:value={notes} rows="2" disabled={!editable}></textarea>
			</label>
		</div>

		<!-- Line items -->
		<div class="lines-section">
			<div class="lines-head">
				<span class="lines-title">Line Items</span>
				{#if editable}
					<button type="button" class="btn-add-line" onclick={addLine}>+ Add line</button>
				{/if}
			</div>

			{#if lines.length === 0}
				<p class="muted">No line items.</p>
			{:else}
				<table class="lines-table">
					<thead>
						<tr>
							<th>Description</th>
							<th class="right">Qty</th>
							<th class="right">Unit Price</th>
							<th>UoM</th>
							<th>GL</th>
							<th class="right">Total</th>
							{#if editable}<th></th>{/if}
						</tr>
					</thead>
					<tbody>
						{#each lines as l, i (i)}
							<tr>
								<td>
									{#if editable}
										<input type="text" bind:value={l.description} placeholder="Item description" />
									{:else}
										{l.description || '—'}
									{/if}
								</td>
								<td class="right">
									{#if editable}
										<input
											type="number"
											step="0.0001"
											min="0"
											class="num"
											value={l.quantity ?? ''}
											oninput={(e) => (l.quantity = numOrNull(e.currentTarget.value))}
										/>
									{:else}
										{l.quantity ?? '—'}
									{/if}
								</td>
								<td class="right">
									{#if editable}
										<input
											type="number"
											step="0.01"
											min="0"
											class="num"
											value={l.unit_price ?? ''}
											oninput={(e) => (l.unit_price = numOrNull(e.currentTarget.value))}
										/>
									{:else}
										{l.unit_price ?? '—'}
									{/if}
								</td>
								<td>
									{#if editable}
										<input type="text" class="uom" bind:value={l.uom} placeholder="ea" />
									{:else}
										{l.uom || '—'}
									{/if}
								</td>
								<td>
									{#if editable}
										<select bind:value={l.gl_account_id}>
											<option value="">—</option>
											{#each glAccounts as g (g.id)}
												<option value={g.id}>{g.code}</option>
											{/each}
										</select>
									{:else}
										{glLabel(l.gl_account_id)}
									{/if}
								</td>
								<td class="right mono">{formatMoney(lineTotal(l), { currency })}</td>
								{#if editable}
									<td>
										<button type="button" class="btn-remove-line" onclick={() => removeLine(i)} aria-label="Remove line">×</button>
									</td>
								{/if}
							</tr>
						{/each}
					</tbody>
					<tfoot>
						<tr>
							<td colspan={editable ? 5 : 5} class="right total-label">Total</td>
							<td class="right mono total-value"><Money amount={computedTotal} {currency} /></td>
							{#if editable}<td></td>{/if}
						</tr>
					</tfoot>
				</table>
			{/if}
		</div>

		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={onclose}>Close</button>
			{#if editable}
				<button type="submit" class="btn-primary" disabled={saving || !requisition_number.trim()}>
					{saving ? 'Saving…' : isCreate ? 'Create' : 'Save'}
				</button>
			{/if}
		</div>
	</form>
</Modal>

<style>
	.status-row {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-bottom: 12px;
	}
	.muted {
		font-size: 0.82rem;
		color: var(--text-muted);
	}

	.badge {
		display: inline-block;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.badge.draft { background: rgba(99, 140, 255, 0.15); color: #638cff; }
	.badge.submitted { background: rgba(212, 148, 10, 0.15); color: #d4940a; }
	.badge.pending_approval { background: rgba(212, 148, 10, 0.15); color: #d4940a; }
	.badge.approved { background: rgba(31, 168, 106, 0.15); color: #1fa86a; }
	.badge.rejected { background: rgba(224, 64, 64, 0.15); color: #e04040; }
	.badge.converted { background: rgba(140, 100, 240, 0.15); color: #8c64f0; }
	.badge.cancelled { background: var(--bg); color: var(--text-muted); }

	.form-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 12px;
	}
	.form-grid label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.82rem;
		color: var(--text-muted);
	}
	.form-grid label.full-width {
		grid-column: 1 / -1;
	}
	.form-grid input,
	.form-grid textarea {
		padding: 7px 9px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.88rem;
	}
	.form-grid input:disabled,
	.form-grid textarea:disabled {
		opacity: 0.7;
		cursor: not-allowed;
	}

	.lines-section {
		margin-top: 18px;
	}
	.lines-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 8px;
	}
	.lines-title {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--text);
	}
	.btn-add-line {
		padding: 4px 10px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-add-line:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.lines-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.82rem;
	}
	.lines-table th,
	.lines-table td {
		padding: 5px 6px;
		border-bottom: 1px solid var(--border);
		text-align: left;
	}
	.lines-table th.right,
	.lines-table td.right {
		text-align: right;
	}
	.lines-table input,
	.lines-table select {
		width: 100%;
		padding: 5px 6px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.82rem;
	}
	.lines-table input.num {
		text-align: right;
		max-width: 90px;
	}
	.lines-table input.uom {
		max-width: 60px;
	}
	.btn-remove-line {
		border: none;
		background: none;
		color: var(--text-muted);
		font-size: 1.1rem;
		line-height: 1;
		cursor: pointer;
	}
	.btn-remove-line:hover {
		color: #e04040;
	}
	.total-label {
		font-weight: 600;
		color: var(--text-muted);
	}
	.total-value {
		font-weight: 600;
	}
	.mono {
		font-variant-numeric: tabular-nums;
	}
</style>
