<script lang="ts">
	import type {
		Reconciliation,
		ReconLine,
		ReconStatus,
		ReconClassification,
		StatementLineInput
	} from '$lib/types/vendorStatementRecon';
	import {
		RECON_STATUS_LABELS,
		RECON_CLASSIFICATION_LABELS,
		RECON_RESOLUTION_LABELS
	} from '$lib/types/vendorStatementRecon';
	import { auth } from '$lib/stores/auth.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import { formatDate } from '$lib/utils/time';
	import {
		createReconciliation,
		uploadReconciliation,
		resolveLine,
		getReconciliation
	} from '$lib/api/vendorStatementRecon';

	interface VendorOption {
		id: string;
		name: string;
	}

	let {
		recon,
		vendors,
		onclose,
		onsaved
	}: {
		// null → create mode; a Reconciliation → detail/diff mode.
		recon: Reconciliation | null;
		vendors: VendorOption[];
		onclose: () => void;
		onsaved: (r: Reconciliation) => void;
	} = $props();

	const isCreate = $derived(recon === null);
	// create + line resolution = admin/ap_manager.
	const canEdit = $derived(auth.isManager);

	// --- Detail-mode local copy (so resolve actions refresh in place) ---
	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot */
	let detail = $state<Reconciliation | null>(recon);
	/* eslint-enable svelte/state-referenced-locally */

	// --- Create-mode fields ---
	let vendor_id = $state('');
	let statement_date = $state('');
	let statement_reference = $state('');
	let currency = $state('USD');
	let notes = $state('');
	// Intake choice: pasted lines (default) or a CSV file upload.
	let file = $state<File | null>(null);
	let lines = $state<StatementLineInput[]>([{ invoice_number: '', amount: null, invoice_date: '' }]);

	let saving = $state(false);
	let busyLineId = $state<string | null>(null);

	function numOrNull(v: unknown): number | null {
		if (v === '' || v === null || v === undefined) return null;
		const n = parseFloat(String(v));
		return Number.isFinite(n) ? n : null;
	}

	function addLine() {
		lines = [...lines, { invoice_number: '', amount: null, invoice_date: '' }];
	}

	function removeLine(idx: number) {
		lines = lines.filter((_, i) => i !== idx);
		if (lines.length === 0) addLine();
	}

	function onFile(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		file = input.files?.[0] ?? null;
	}

	function handleError(err: unknown, fallback: string) {
		toast(err instanceof Error ? err.message : fallback, 'error');
	}

	async function handleCreate() {
		if (!vendor_id || !statement_date) return;
		saving = true;
		try {
			let saved: Reconciliation;
			if (file) {
				saved = await uploadReconciliation(file, {
					vendor_id,
					statement_date,
					statement_reference: statement_reference.trim() || undefined,
					currency: currency.trim() || undefined
				});
			} else {
				const payloadLines = lines
					.filter((l) => (l.invoice_number ?? '').toString().trim() || l.amount != null)
					.map((l) => ({
						invoice_number: (l.invoice_number ?? '').toString().trim() || null,
						invoice_date: l.invoice_date || null,
						amount: l.amount ?? null,
						status: l.status ?? null
					}));
				saved = await createReconciliation({
					vendor_id,
					statement_date,
					statement_reference: statement_reference.trim() || null,
					currency: currency.trim() || 'USD',
					notes: notes.trim() || null,
					lines: payloadLines
				});
			}
			toast(m('vendorStatements.modal.toastCreated'), 'success');
			onsaved(saved);
			onclose();
		} catch (err) {
			handleError(err, m('vendorStatements.modal.toastCreateFailed'));
		} finally {
			saving = false;
		}
	}

	async function runResolve(line: ReconLine, status: 'resolved' | 'ignored') {
		if (!detail) return;
		busyLineId = line.id;
		try {
			const updated = await resolveLine(detail.id, line.id, { resolution_status: status });
			detail = updated;
			onsaved(updated);
			toast(
				status === 'resolved'
					? m('vendorStatements.modal.toastResolved')
					: m('vendorStatements.modal.toastIgnored'),
				'success'
			);
		} catch (err) {
			// Refresh from the server so the modal doesn't show stale state.
			try {
				if (detail) detail = await getReconciliation(detail.id);
			} catch {
				/* keep the existing snapshot */
			}
			handleError(err, m('vendorStatements.modal.toastUpdateFailed'));
		} finally {
			busyLineId = null;
		}
	}

	function isActionable(line: ReconLine): boolean {
		return (
			line.resolution_status === 'unresolved' &&
			(line.classification === 'missing_on_our_side' ||
				line.classification === 'amount_mismatch')
		);
	}

	function classificationTone(c: ReconClassification): string {
		// matched = neutral/green; everything else flags a discrepancy.
		return c === 'matched' ? 'ok' : c === 'amount_mismatch' ? 'warn' : 'flag';
	}

	const status = $derived<ReconStatus>(detail?.status ?? 'open');
	const sortedLines = $derived(detail?.lines ?? []);

	const modalTitle = $derived(
		isCreate
			? m('vendorStatements.modal.titleCreate')
			: m('vendorStatements.modal.titleDetail', {
					vendor: detail?.vendor_name ?? m('vendorStatements.modal.statementFallback')
				})
	);
	const ariaLabel = $derived(
		isCreate ? m('vendorStatements.modal.ariaCreate') : m('vendorStatements.modal.ariaDetail')
	);
</script>

<Modal open {ariaLabel} title={modalTitle} width="lg" {onclose}>
	{#if isCreate}
		<form onsubmit={(e) => { e.preventDefault(); handleCreate(); }}>
			<div class="form-grid">
				<label>
					<span>{m('vendorStatements.modal.vendor')} <em class="required">*</em></span>
					<select bind:value={vendor_id} required disabled={!canEdit}>
						<option value="">{m('vendorStatements.modal.selectVendor')}</option>
						{#each vendors as v (v.id)}
							<option value={v.id}>{v.name}</option>
						{/each}
					</select>
				</label>
				<label>
					<span>{m('vendorStatements.modal.statementDate')} <em class="required">*</em></span>
					<input type="date" bind:value={statement_date} required disabled={!canEdit} />
				</label>
				<label>
					<span>{m('vendorStatements.modal.statementReference')}</span>
					<input type="text" bind:value={statement_reference} disabled={!canEdit} />
				</label>
				<label>
					<span>{m('vendorStatements.modal.currency')}</span>
					<input type="text" bind:value={currency} maxlength="3" disabled={!canEdit} />
				</label>
				<label class="full-width">
					<span>{m('vendorStatements.modal.notes')}</span>
					<input type="text" bind:value={notes} disabled={!canEdit} />
				</label>
			</div>

			<div class="intake-section">
				<div class="intake-title">{m('vendorStatements.modal.statementLines')}</div>
				<p class="intake-hint">
					{m('vendorStatements.modal.intakeHintPre')}<strong
						>{m('vendorStatements.modal.intakeHintOr')}</strong
					>{m('vendorStatements.modal.intakeHintPost')}
				</p>

				<div class="lines-editor" aria-label={m('vendorStatements.modal.linesEditorAria')}>
					<div class="line-head">
						<span>{m('vendorStatements.modal.colInvoice')}</span>
						<span>{m('vendorStatements.modal.colAmount')}</span>
						<span>{m('vendorStatements.modal.colDate')}</span>
						<span></span>
					</div>
					{#each lines as line, idx (idx)}
						<div class="line-row">
							<input
								type="text"
								bind:value={line.invoice_number}
								placeholder="INV-1001"
								aria-label={m('vendorStatements.modal.lineInvoiceAria', { n: idx + 1 })}
								disabled={!canEdit}
							/>
							<input
								type="number"
								step="0.01"
								min="0"
								value={line.amount ?? ''}
								oninput={(e) => (line.amount = numOrNull(e.currentTarget.value))}
								placeholder="0.00"
								aria-label={m('vendorStatements.modal.lineAmountAria', { n: idx + 1 })}
								disabled={!canEdit}
							/>
							<input
								type="date"
								bind:value={line.invoice_date}
								aria-label={m('vendorStatements.modal.lineDateAria', { n: idx + 1 })}
								disabled={!canEdit}
							/>
							<button
								type="button"
								class="line-remove"
								onclick={() => removeLine(idx)}
								aria-label={m('vendorStatements.modal.removeLineAria', { n: idx + 1 })}
								disabled={!canEdit}
							>
								×
							</button>
						</div>
					{/each}
					{#if canEdit}
						<button type="button" class="line-add" onclick={addLine}
							>{m('vendorStatements.modal.addLine')}</button
						>
					{/if}
				</div>

				<label class="file-label">
					<span>{m('vendorStatements.modal.uploadCsv')}</span>
					<input
						type="file"
						accept=".csv,text/csv,application/pdf"
						onchange={onFile}
						aria-label={m('vendorStatements.modal.fileAria')}
						disabled={!canEdit}
					/>
				</label>
			</div>

			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={onclose}
					>{m('vendorStatements.modal.cancel')}</button
				>
				{#if canEdit}
					<button type="submit" class="btn-primary" disabled={saving || !vendor_id || !statement_date}>
						{saving
							? m('vendorStatements.modal.reconciling')
							: m('vendorStatements.modal.reconcile')}
					</button>
				{/if}
			</div>
		</form>
	{:else if detail}
		<!-- Detail / diff view -->
		<div class="status-row">
			<span class="badge {status}">{RECON_STATUS_LABELS[status]}</span>
			<span class="meta-pill">{formatDate(detail.statement_date)}</span>
			{#if detail.statement_reference}
				<span class="meta-pill"
					>{m('vendorStatements.modal.refPill', { reference: detail.statement_reference })}</span
				>
			{/if}
		</div>

		<!-- Summary counts -->
		<div class="stat-chips">
			<span class="stat-chip"
				>{m('vendorStatements.modal.statLines', { n: detail.summary.line_count })}</span
			>
			<span class="stat-chip ok"
				>{m('vendorStatements.modal.statMatched', { n: detail.summary.matched_count })}</span
			>
			<span class="stat-chip warn"
				>{m('vendorStatements.modal.statMismatch', { n: detail.summary.amount_mismatch_count })}</span
			>
			<span class="stat-chip flag"
				>{m('vendorStatements.modal.statMissingOurs', {
					n: detail.summary.missing_our_side_count
				})}</span
			>
			<span class="stat-chip flag"
				>{m('vendorStatements.modal.statMissingTheirs', {
					n: detail.summary.missing_their_side_count
				})}</span
			>
		</div>

		<div class="totals-row">
			<div class="total-box">
				<span class="total-label">{m('vendorStatements.modal.statementTotal')}</span>
				<span class="total-value">
					<Money amount={detail.summary.statement_total} currency={detail.currency} mono />
				</span>
			</div>
			<div class="total-box">
				<span class="total-label">{m('vendorStatements.modal.ledgerTotal')}</span>
				<span class="total-value">
					<Money amount={detail.summary.ledger_total} currency={detail.currency} mono />
				</span>
			</div>
		</div>

		<!-- Side-by-side diff table -->
		<div class="diff-section">
			<table class="diff-table">
				<thead>
					<tr>
						<th>{m('vendorStatements.modal.thStatementInv')}</th>
						<th class="right">{m('vendorStatements.modal.thStatementAmount')}</th>
						<th class="right">{m('vendorStatements.modal.thOurAmount')}</th>
						<th class="right">{m('vendorStatements.modal.thDifference')}</th>
						<th>{m('vendorStatements.modal.thClassification')}</th>
						<th>{m('vendorStatements.modal.thResolution')}</th>
					</tr>
				</thead>
				<tbody>
					{#if sortedLines.length === 0}
						<tr><td colspan="6" class="diff-empty">{m('vendorStatements.modal.noLines')}</td></tr>
					{/if}
					{#each sortedLines as line (line.id)}
						<tr>
							<td class="mono">{line.statement_invoice_number ?? '—'}</td>
							<td class="right mono">
								<Money amount={line.statement_amount} currency={detail.currency} />
							</td>
							<td class="right mono">
								{#if line.matched_invoice_number}
									<span class="ledger-ref">{line.matched_invoice_number}</span>
								{/if}
								<Money amount={line.ledger_amount} currency={detail.currency} />
							</td>
							<td class="right mono">
								<Money amount={line.amount_difference} currency={detail.currency} accounting />
							</td>
							<td>
								<span class="cls {classificationTone(line.classification)}">
									{RECON_CLASSIFICATION_LABELS[line.classification]}
								</span>
							</td>
							<td class="resolution-cell">
								{#if isActionable(line) && canEdit}
									<RowAction
										variant="success"
										disabled={busyLineId === line.id}
										onclick={() => runResolve(line, 'resolved')}
										ariaLabel={m('vendorStatements.modal.resolveAria', {
											line: line.statement_invoice_number ?? line.id
										})}
									>
										{m('vendorStatements.modal.resolve')}
									</RowAction>
									<RowAction
										disabled={busyLineId === line.id}
										onclick={() => runResolve(line, 'ignored')}
										ariaLabel={m('vendorStatements.modal.ignoreAria', {
											line: line.statement_invoice_number ?? line.id
										})}
									>
										{m('vendorStatements.modal.ignore')}
									</RowAction>
								{:else}
									<span class="res-state {line.resolution_status}">
										{RECON_RESOLUTION_LABELS[line.resolution_status]}
									</span>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={onclose}
				>{m('vendorStatements.modal.close')}</button
			>
		</div>
	{/if}
</Modal>

<style>
	.status-row {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 12px;
		flex-wrap: wrap;
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
	.badge.open {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}
	.badge.resolved {
		background: rgba(31, 168, 106, 0.15);
		color: #1fa86a;
	}

	.meta-pill {
		font-size: 0.72rem;
		padding: 2px 8px;
		border-radius: 8px;
		background: var(--bg);
		color: var(--text-muted);
	}

	/* --- Summary stat chips --- */
	.stat-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin-bottom: 12px;
	}
	.stat-chip {
		font-size: 0.72rem;
		font-weight: 600;
		padding: 3px 9px;
		border-radius: 8px;
		background: var(--bg);
		color: var(--text-muted);
	}
	.stat-chip.ok {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}
	.stat-chip.warn {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}
	.stat-chip.flag {
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
	}

	/* --- Totals --- */
	.totals-row {
		display: flex;
		gap: 12px;
		margin-bottom: 16px;
	}
	.total-box {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 8px 12px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--bg);
	}
	.total-label {
		font-size: 0.72rem;
		color: var(--text-muted);
	}
	.total-value {
		font-size: 0.95rem;
		font-weight: 600;
	}

	/* --- Diff table --- */
	.diff-section {
		margin-top: 4px;
	}
	.diff-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.82rem;
	}
	.diff-table th {
		text-align: left;
		padding: 5px 6px;
		color: var(--text-muted);
		font-weight: 500;
		border-bottom: 1px solid var(--border);
	}
	.diff-table th.right {
		text-align: right;
	}
	.diff-table td {
		padding: 6px;
		border-bottom: 1px solid var(--border);
		vertical-align: middle;
	}
	.diff-table td.right {
		text-align: right;
	}
	.diff-empty {
		text-align: center;
		color: var(--text-muted);
		padding: 16px;
	}
	.mono {
		font-variant-numeric: tabular-nums;
		font-family: ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
	}
	.ledger-ref {
		display: block;
		font-size: 0.7rem;
		color: var(--text-muted);
	}

	.cls {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 8px;
		font-size: 0.7rem;
		font-weight: 600;
	}
	.cls.ok {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}
	.cls.warn {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}
	.cls.flag {
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
	}

	.resolution-cell {
		display: flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
	}
	.res-state {
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--text-muted);
	}
	.res-state.resolved {
		color: #1fa86a;
	}
	.res-state.ignored {
		color: var(--text-muted);
		font-style: italic;
	}

	/* --- Create form --- */
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
	.form-grid select {
		padding: 7px 9px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.88rem;
	}
	.form-grid input:disabled,
	.form-grid select:disabled {
		opacity: 0.7;
		cursor: not-allowed;
	}

	.intake-section {
		margin-top: 16px;
	}
	.intake-title {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text);
		margin-bottom: 4px;
	}
	.intake-hint {
		font-size: 0.78rem;
		color: var(--text-muted);
		margin: 0 0 10px;
	}

	.lines-editor {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.line-head,
	.line-row {
		display: grid;
		grid-template-columns: 1.4fr 1fr 1fr 32px;
		gap: 8px;
		align-items: center;
	}
	.line-head {
		font-size: 0.72rem;
		color: var(--text-muted);
	}
	.line-row input {
		padding: 6px 8px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.84rem;
	}
	.line-remove {
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		border-radius: 5px;
		height: 30px;
		cursor: pointer;
		font-size: 1rem;
		line-height: 1;
	}
	.line-remove:hover:not(:disabled) {
		border-color: #e04040;
		color: #e04040;
	}
	.line-remove:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.line-add {
		align-self: flex-start;
		margin-top: 2px;
		border: 1px dashed var(--border);
		background: transparent;
		color: var(--text-muted);
		border-radius: 5px;
		padding: 5px 12px;
		cursor: pointer;
		font-family: inherit;
		font-size: 0.8rem;
	}
	.line-add:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.file-label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		margin-top: 14px;
		font-size: 0.82rem;
		color: var(--text-muted);
	}
</style>
