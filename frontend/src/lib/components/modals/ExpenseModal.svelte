<script lang="ts">
	import type { Expense } from '$lib/types/expense';
	import {
		EXPENSE_PAYMENT_METHODS,
		EXPENSE_PAYMENT_METHOD_LABELS,
		EXPENSE_STATUS_LABELS
	} from '$lib/types/expense';
	import { auth } from '$lib/stores/auth.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import {
		createExpense,
		updateExpense,
		uploadReceipt,
		receiptUrl,
		type GlAccountOption
	} from '$lib/api/expenses';

	let {
		expense,
		glAccounts,
		onclose,
		onsaved
	}: {
		// null → create mode; an Expense → detail/edit mode.
		expense: Expense | null;
		glAccounts: GlAccountOption[];
		onclose: () => void;
		onsaved: (e: Expense) => void;
	} = $props();

	const isCreate = $derived(expense === null);
	// create / update / receipt upload = admin / ap_manager / ap_clerk.
	const canEdit = $derived(auth.hasAnyRole('admin', 'ap_manager', 'ap_clerk'));

	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot */
	let expense_date = $state(expense?.expense_date ?? '');
	let merchant = $state(expense?.merchant ?? '');
	let category = $state(expense?.category ?? '');
	let amount = $state<number | null>(expense?.amount ?? null);
	let currency = $state(expense?.currency ?? 'USD');
	let payment_method = $state(expense?.payment_method ?? 'out_of_pocket');
	let gl_account_id = $state(expense?.gl_account_id ?? '');
	let description = $state(expense?.description ?? '');
	let reimbursable = $state(expense?.reimbursable ?? true);
	/* eslint-enable svelte/state-referenced-locally */

	let saving = $state(false);

	// Receipt: on create we stash the File and upload after the create returns
	// an id; on edit we upload immediately (the id already exists).
	let fileInput = $state<HTMLInputElement>();
	let pendingFile = $state<File | null>(null);
	let uploading = $state(false);

	const status = $derived(expense?.status ?? 'draft');

	function numOrNull(v: unknown): number | null {
		if (v === '' || v === null || v === undefined) return null;
		const n = parseFloat(String(v));
		return Number.isFinite(n) ? n : null;
	}

	function handleError(err: unknown, fallback: string) {
		toast(err instanceof Error ? err.message : fallback, 'error');
	}

	function onFilePick(e: Event) {
		const input = e.target as HTMLInputElement;
		const file = input.files?.[0] ?? null;
		if (!file) return;
		if (isCreate || !expense) {
			// Defer — upload after the create call returns an id.
			pendingFile = file;
		} else {
			uploadNow(expense.id, file);
		}
	}

	async function uploadNow(id: string, file: File) {
		uploading = true;
		try {
			const updated = await uploadReceipt(id, file);
			toast('Receipt uploaded', 'success');
			onsaved(updated);
		} catch (err) {
			handleError(err, 'Upload failed');
		} finally {
			uploading = false;
			if (fileInput) fileInput.value = '';
		}
	}

	async function handleSave() {
		if (!merchant.trim() || amount == null) return;
		saving = true;
		try {
			const payload = {
				expense_date: expense_date || null,
				merchant: merchant.trim(),
				category: category.trim() || null,
				amount,
				currency: currency.trim() || 'USD',
				payment_method,
				gl_account_id: gl_account_id || null,
				description: description.trim() || null,
				reimbursable
			};
			let saved = isCreate
				? await createExpense(payload)
				: await updateExpense(expense!.id, payload);
			// A receipt staged during create is uploaded once the id exists.
			if (pendingFile) {
				saved = await uploadReceipt(saved.id, pendingFile);
				pendingFile = null;
			}
			toast(isCreate ? 'Expense created' : 'Expense saved', 'success');
			onsaved(saved);
			onclose();
		} catch (err) {
			handleError(err, isCreate ? 'Create failed' : 'Save failed');
		} finally {
			saving = false;
		}
	}

	async function viewReceipt() {
		if (!expense?.receipt_file_key) return;
		try {
			const url = await receiptUrl(expense.receipt_file_key);
			window.open(url, '_blank');
			setTimeout(() => URL.revokeObjectURL(url), 60_000);
		} catch (err) {
			handleError(err, 'Could not load receipt');
		}
	}

	const modalTitle = $derived(
		isCreate
			? 'New Expense'
			: canEdit
				? `Edit Expense — ${expense!.merchant ?? expense!.id.slice(0, 8)}`
				: `Expense — ${expense!.merchant ?? expense!.id.slice(0, 8)}`
	);
	const ariaLabel = $derived(isCreate ? 'New expense' : 'Expense detail');
</script>

<Modal open {ariaLabel} title={modalTitle} width="lg" {onclose}>
	<form onsubmit={(e) => { e.preventDefault(); handleSave(); }}>
		{#if !isCreate}
			<div class="status-row">
				<span class="badge {status}">{EXPENSE_STATUS_LABELS[status as keyof typeof EXPENSE_STATUS_LABELS] ?? status}</span>
			</div>
		{/if}

		<div class="form-grid">
			<label>
				<span>Date</span>
				<input type="date" bind:value={expense_date} disabled={!canEdit} />
			</label>
			<label>
				<span>Merchant <em class="required">*</em></span>
				<input type="text" bind:value={merchant} required disabled={!canEdit} />
			</label>
			<label>
				<span>Amount <em class="required">*</em></span>
				<input
					type="number"
					step="0.01"
					min="0"
					value={amount ?? ''}
					oninput={(e) => (amount = numOrNull(e.currentTarget.value))}
					disabled={!canEdit}
				/>
			</label>
			<label>
				<span>Currency</span>
				<input type="text" bind:value={currency} maxlength="3" disabled={!canEdit} />
			</label>
			<label>
				<span>Category</span>
				<input type="text" bind:value={category} placeholder="e.g. meals, travel" disabled={!canEdit} />
			</label>
			<label>
				<span>Payment Method</span>
				<select bind:value={payment_method} disabled={!canEdit}>
					{#each EXPENSE_PAYMENT_METHODS as m}
						<option value={m}>{EXPENSE_PAYMENT_METHOD_LABELS[m]}</option>
					{/each}
				</select>
			</label>
			<label>
				<span>GL Account</span>
				<select bind:value={gl_account_id} disabled={!canEdit}>
					<option value="">Select…</option>
					{#each glAccounts as g (g.id)}
						<option value={g.id}>{g.code} — {g.name}</option>
					{/each}
				</select>
			</label>
			<label class="checkbox-label">
				<input type="checkbox" bind:checked={reimbursable} disabled={!canEdit} />
				<span>Reimbursable</span>
			</label>
			<label class="full-width">
				<span>Description</span>
				<textarea bind:value={description} rows="2" disabled={!canEdit}></textarea>
			</label>
		</div>

		<!-- Receipt -->
		<div class="receipt-section">
			<span class="receipt-title">Receipt</span>
			{#if expense?.receipt_file_key}
				<button type="button" class="btn-doc" onclick={viewReceipt}>View receipt</button>
			{:else if pendingFile}
				<span class="receipt-empty">{pendingFile.name} (uploads on save)</span>
			{:else}
				<span class="receipt-empty">No receipt attached.</span>
			{/if}
			{#if canEdit}
				<input
					type="file"
					accept=".pdf,.png,.jpg,.jpeg,.tiff,.heic,.webp"
					bind:this={fileInput}
					onchange={onFilePick}
					hidden
				/>
				<button
					type="button"
					class="btn-doc-upload"
					disabled={uploading}
					onclick={() => fileInput?.click()}
				>
					{uploading ? 'Uploading…' : expense?.receipt_file_key ? 'Replace' : 'Attach'}
				</button>
			{/if}
		</div>

		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={onclose}>Close</button>
			{#if canEdit}
				<button type="submit" class="btn-primary" disabled={saving}>
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
		gap: 8px;
		margin-bottom: 12px;
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
	.badge.approved { background: rgba(31, 168, 106, 0.15); color: #1fa86a; }
	.badge.rejected { background: rgba(224, 64, 64, 0.15); color: #e04040; }
	.badge.reimbursed { background: rgba(140, 100, 240, 0.15); color: #8c64f0; }

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

	.form-grid label.checkbox-label {
		flex-direction: row;
		align-items: center;
		gap: 8px;
	}

	.form-grid input,
	.form-grid select,
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
	.form-grid select:disabled,
	.form-grid textarea:disabled {
		opacity: 0.7;
		cursor: not-allowed;
	}

	.receipt-section {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-top: 16px;
		flex-wrap: wrap;
	}
	.receipt-title {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text);
	}
	.receipt-empty {
		font-size: 0.82rem;
		color: var(--text-muted);
	}
	.btn-doc,
	.btn-doc-upload {
		padding: 5px 12px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-doc:hover,
	.btn-doc-upload:hover {
		border-color: var(--accent);
		color: var(--accent);
	}
	.btn-doc-upload:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
</style>
