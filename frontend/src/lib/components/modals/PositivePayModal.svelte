<script lang="ts">
	import type {
		PositivePayFile,
		PositivePayFileType,
		BankFormat,
		PresentedItemInput
	} from '$lib/types/positivePay';
	import {
		POSITIVE_PAY_FILE_TYPE_LABELS,
		POSITIVE_PAY_STATUS_LABELS,
		BANK_FORMATS,
		BANK_FORMAT_LABELS
	} from '$lib/types/positivePay';
	import { auth } from '$lib/stores/auth.svelte';
	import { api } from '$lib/api';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import {
		generateCheckIssue,
		generateAchAuthorization,
		processReturn,
		getPositivePayFile,
		downloadPositivePayFile
	} from '$lib/api/positivePay';

	interface RunOption {
		id: string;
		status: string;
		executed_at: string | null;
		total_amount: number | null;
	}

	let {
		file,
		onclose,
		onsaved
	}: {
		// null → create/generate mode; a PositivePayFile → detail mode.
		file: PositivePayFile | null;
		onclose: () => void;
		onsaved: (f: PositivePayFile) => void;
	} = $props();

	const isCreate = $derived(file === null);
	// generate + process-return = admin/ap_manager.
	const canEdit = $derived(auth.isManager);

	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot */
	let detail = $state<PositivePayFile | null>(file);
	/* eslint-enable svelte/state-referenced-locally */

	// --- Create-mode fields ---
	let fileType = $state<PositivePayFileType>('check_issue');
	let bankFormat = $state<BankFormat>('csv');
	let runId = $state('');
	let runs = $state<RunOption[]>([]);
	let saving = $state(false);

	// --- Return-processing sub-form (detail mode) ---
	let presentedText = $state('');
	let processing = $state(false);

	function handleError(err: unknown, fallback: string) {
		toast(err instanceof Error ? err.message : fallback, 'error');
	}

	$effect(() => {
		if (isCreate && canEdit) loadRuns();
	});

	async function loadRuns() {
		try {
			const data = await api.get<{ items: RunOption[] }>('/api/payments/runs/?page_size=100');
			runs = data.items ?? [];
		} catch {
			/* non-critical — manager can still type a run id */
		}
	}

	async function handleCreate() {
		if (fileType === 'check_issue' && !runId.trim()) return;
		saving = true;
		try {
			const saved =
				fileType === 'check_issue'
					? await generateCheckIssue(runId.trim(), bankFormat)
					: await generateAchAuthorization(bankFormat);
			toast('Positive Pay file generated', 'success');
			onsaved(saved);
			onclose();
		} catch (err) {
			handleError(err, 'Generation failed');
		} finally {
			saving = false;
		}
	}

	// Parse the pasted presented-items block. One item per line:
	//   <check_number>,<amount>   (amount optional). Blank lines skipped.
	function parsePresented(): PresentedItemInput[] {
		const items: PresentedItemInput[] = [];
		for (const raw of presentedText.split('\n')) {
			const line = raw.trim();
			if (!line) continue;
			const [num, amt] = line.split(',').map((s) => s.trim());
			const amount = amt !== undefined && amt !== '' ? Number.parseFloat(amt) : null;
			items.push({
				check_number: num || null,
				amount: amount !== null && Number.isFinite(amount) ? amount : null
			});
		}
		return items;
	}

	async function handleProcessReturn() {
		if (!detail) return;
		const items = parsePresented();
		if (items.length === 0) {
			toast('Paste at least one presented item (check#,amount per line)', 'error');
			return;
		}
		processing = true;
		try {
			const result = await processReturn(detail.id, items);
			detail = result.file;
			onsaved(result.file);
			presentedText = '';
			const flagged = result.amount_mismatches + result.not_on_file;
			toast(
				flagged > 0
					? `Return processed — ${flagged} flagged, ${result.exceptions_created} exception(s) raised`
					: 'Return processed — no discrepancies',
				flagged > 0 ? 'error' : 'success'
			);
		} catch (err) {
			// Refresh so the modal doesn't show stale state.
			try {
				if (detail) detail = await getPositivePayFile(detail.id);
			} catch {
				/* keep the existing snapshot */
			}
			handleError(err, 'Could not process return');
		} finally {
			processing = false;
		}
	}

	async function handleDownload() {
		if (!detail) return;
		const ext = detail.bank_format === 'fixed_width' ? 'txt' : 'csv';
		const filename = `positive-pay-${detail.file_type}-${detail.id}.${ext}`;
		try {
			await downloadPositivePayFile(detail.id, filename);
		} catch (err) {
			handleError(err, 'Download failed');
		}
	}

	function formatDate(s: string | null): string {
		if (!s) return '—';
		const d = new Date(s);
		if (Number.isNaN(d.getTime())) return s;
		return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
	}

	function fileTypeLabel(t: string): string {
		return POSITIVE_PAY_FILE_TYPE_LABELS[t as PositivePayFileType] ?? t;
	}

	const returnSummary = $derived(detail?.meta?.return_summary ?? null);

	const modalTitle = $derived(
		isCreate ? 'Generate Positive Pay File' : `Positive Pay — ${fileTypeLabel(detail?.file_type ?? '')}`
	);
	const ariaLabel = $derived(
		isCreate ? 'Generate positive pay file' : 'Positive pay file detail'
	);
</script>

<Modal open {ariaLabel} title={modalTitle} width="lg" {onclose}>
	{#if isCreate}
		<form onsubmit={(e) => { e.preventDefault(); handleCreate(); }}>
			<div class="form-grid">
				<label>
					<span>File type <em class="required">*</em></span>
					<select bind:value={fileType} disabled={!canEdit}>
						<option value="check_issue">Check issue (per payment run)</option>
						<option value="ach_authorization">ACH authorization (org-wide)</option>
					</select>
				</label>
				<label>
					<span>Bank format</span>
					<select bind:value={bankFormat} disabled={!canEdit}>
						{#each BANK_FORMATS as fmt (fmt)}
							<option value={fmt}>{BANK_FORMAT_LABELS[fmt]}</option>
						{/each}
					</select>
				</label>
				{#if fileType === 'check_issue'}
					<label class="full-width">
						<span>Payment run <em class="required">*</em></span>
						{#if runs.length > 0}
							<select bind:value={runId} required disabled={!canEdit}>
								<option value="">Select a payment run…</option>
								{#each runs as run (run.id)}
									<option value={run.id}>
										{run.id.slice(0, 8)} · {run.status}{run.executed_at
											? ` · ${formatDate(run.executed_at)}`
											: ''}
									</option>
								{/each}
							</select>
						{:else}
							<input
								type="text"
								bind:value={runId}
								placeholder="Payment run id (UUID)"
								aria-label="Payment run id"
								required
								disabled={!canEdit}
							/>
						{/if}
					</label>
				{/if}
			</div>

			<p class="intake-hint">
				{#if fileType === 'check_issue'}
					Renders every cheque in the selected run into the bank's Positive Pay format.
					Generation is idempotent per (run, format) — re-running returns the existing file.
				{:else}
					Lists every active vendor with ACH bank details as an authorized originator for
					debit-block filtering.
				{/if}
			</p>

			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={onclose}>Cancel</button>
				{#if canEdit}
					<button
						type="submit"
						class="btn-primary"
						disabled={saving || (fileType === 'check_issue' && !runId.trim())}
					>
						{saving ? 'Generating…' : 'Generate'}
					</button>
				{/if}
			</div>
		</form>
	{:else if detail}
		<!-- Detail view -->
		<div class="status-row">
			<span class="badge {detail.status}">{POSITIVE_PAY_STATUS_LABELS[detail.status]}</span>
			<span class="meta-pill">{fileTypeLabel(detail.file_type)}</span>
			<span class="meta-pill">{detail.bank_format}</span>
			<span class="meta-pill">{formatDate(detail.created_at)}</span>
		</div>

		<div class="totals-row">
			<div class="total-box">
				<span class="total-label">Items</span>
				<span class="total-value">{detail.item_count}</span>
			</div>
			<div class="total-box">
				<span class="total-label">Total amount</span>
				<span class="total-value"><Money amount={detail.total_amount} mono /></span>
			</div>
			<div class="total-box">
				<span class="total-label">Account</span>
				<span class="total-value mono">
					{detail.account_last4 ? `••••${detail.account_last4}` : '—'}
				</span>
			</div>
		</div>

		<div class="download-row">
			<button type="button" class="btn-cancel" onclick={handleDownload}>
				Download file
			</button>
		</div>

		{#if returnSummary}
			<div class="return-section">
				<div class="section-title">Return summary</div>
				<div class="stat-chips">
					<span class="stat-chip">{returnSummary.presented_count} presented</span>
					<span class="stat-chip ok">{returnSummary.matched_ok} matched</span>
					<span class="stat-chip warn">{returnSummary.amount_mismatches} altered</span>
					<span class="stat-chip flag">{returnSummary.not_on_file} not on file</span>
					<span class="stat-chip flag">{returnSummary.exceptions_created} exceptions</span>
				</div>
				{#if returnSummary.exceptions_created > 0}
					<p class="intake-hint">
						Fraud signals were raised as <a href="/exceptions?type=fraud_flag">fraud exceptions</a> —
						including never-issued cheques (which have no invoice).
					</p>
				{/if}
			</div>
		{/if}

		{#if detail.file_type === 'check_issue' && canEdit}
			<div class="return-section">
				<div class="section-title">Process bank return</div>
				<p class="intake-hint">
					Paste the items the bank reports as presented — one per line, <code>check#,amount</code>.
					Altered or never-issued cheques raise a fraud exception.
				</p>
				<textarea
					bind:value={presentedText}
					rows="5"
					placeholder={'1001,1200.00\n1002,850.00'}
					aria-label="Presented items"
					disabled={processing}
				></textarea>
				<div class="return-actions">
					<button
						type="button"
						class="btn-primary"
						onclick={handleProcessReturn}
						disabled={processing || !presentedText.trim()}
					>
						{processing ? 'Processing…' : 'Process return'}
					</button>
				</div>
			</div>
		{/if}

		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={onclose}>Close</button>
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
	.badge.generated {
		background: rgba(31, 168, 106, 0.15);
		color: #1fa86a;
	}
	.badge.returned_processed {
		background: rgba(212, 148, 10, 0.15);
		color: #d4940a;
	}
	.meta-pill {
		font-size: 0.72rem;
		padding: 2px 8px;
		border-radius: 8px;
		background: var(--bg);
		color: var(--text-muted);
	}

	.totals-row {
		display: flex;
		gap: 12px;
		margin-bottom: 12px;
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

	.download-row {
		margin-bottom: 16px;
	}

	.return-section {
		margin-top: 16px;
		padding-top: 14px;
		border-top: 1px solid var(--border);
	}
	.section-title {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text);
		margin-bottom: 8px;
	}

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
	}
	.diff-table td.right {
		text-align: right;
	}
	.mono {
		font-variant-numeric: tabular-nums;
		font-family: ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
	}

	textarea {
		width: 100%;
		padding: 8px 10px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
		font-size: 0.84rem;
		resize: vertical;
	}
	.return-actions {
		margin-top: 10px;
	}

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

	.intake-hint {
		font-size: 0.78rem;
		color: var(--text-muted);
		margin: 10px 0;
	}
	.intake-hint code {
		font-family: ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
		font-size: 0.74rem;
	}
</style>
