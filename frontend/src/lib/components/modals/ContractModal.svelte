<script lang="ts">
	import type {
		Contract,
		ContractType,
		ContractLineItemInput
	} from '$lib/types/contract';
	import {
		CONTRACT_TYPES,
		CONTRACT_TYPE_LABELS,
		STATUS_LABELS
	} from '$lib/types/contract';
	import { auth } from '$lib/stores/auth.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import {
		createContract,
		updateContract,
		uploadContractFile,
		fetchContractFile,
		activateContract,
		terminateContract,
		cancelContract,
		renewContract,
		createPoFromContract
	} from '$lib/api/contracts';

	interface VendorOption {
		id: string;
		name: string;
	}

	let {
		contract,
		vendors,
		onclose,
		onsaved
	}: {
		// null → create mode; a Contract → detail/edit mode.
		contract: Contract | null;
		vendors: VendorOption[];
		onclose: () => void;
		onsaved: (c: Contract) => void;
	} = $props();

	const isCreate = $derived(contract === null);
	// create/update/lifecycle/upload/create-po = admin/ap_manager.
	const canEdit = $derived(auth.isManager);

	// --- Editable fields (seeded from the contract snapshot in edit mode) ---
	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot */
	let contract_number = $state(contract?.contract_number ?? '');
	let vendor_id = $state(contract?.vendor_id ?? '');
	let title = $state(contract?.title ?? '');
	let description = $state(contract?.description ?? '');
	let contract_type = $state<ContractType>(contract?.contract_type ?? 'service');
	let currency = $state(contract?.currency ?? 'USD');
	let total_value = $state<number | null>(contract?.total_value ?? null);
	let spend_limit = $state<number | null>(contract?.spend_limit ?? null);
	let not_to_exceed = $state(contract?.not_to_exceed ?? false);
	let start_date = $state(contract?.start_date ?? '');
	let end_date = $state(contract?.end_date ?? '');
	let signed_date = $state(contract?.signed_date ?? '');
	let auto_renew = $state(contract?.auto_renew ?? false);
	let renewal_term_months = $state<number | null>(contract?.renewal_term_months ?? null);
	let renewal_notice_days = $state<number>(contract?.renewal_notice_days ?? 30);
	let payment_terms = $state(contract?.payment_terms ?? '');
	let lineItems = $state<ContractLineItemInput[]>(
		(contract?.line_items ?? []).map((li) => ({
			line_number: li.line_number ?? undefined,
			item_code: li.item_code,
			description: li.description,
			quantity: li.quantity,
			unit_price: li.unit_price,
			total: li.total,
			gl_account: li.gl_account
		}))
	);
	/* eslint-enable svelte/state-referenced-locally */

	let saving = $state(false);
	let busy = $state(false);

	const status = $derived(contract?.status ?? 'draft');
	const spend = $derived(contract?.spend ?? null);

	// Lifecycle gating mirrors the backend's legal transitions.
	const canActivate = $derived(status === 'draft');
	const canTerminate = $derived(status === 'active');
	const canCancel = $derived(status === 'draft' || status === 'active');
	const canRenew = $derived(status === 'active' || status === 'expired');
	const canCreatePo = $derived(status === 'active');

	function addLine() {
		lineItems = [
			...lineItems,
			{
				line_number: lineItems.length + 1,
				item_code: null,
				description: '',
				quantity: 1,
				unit_price: null,
				total: null,
				gl_account: null
			}
		];
	}

	function updateLine(idx: number, field: keyof ContractLineItemInput, value: unknown) {
		lineItems = lineItems.map((li, i) => (i === idx ? { ...li, [field]: value } : li));
	}

	function removeLine(idx: number) {
		lineItems = lineItems.filter((_, i) => i !== idx);
	}

	function numOrNull(v: unknown): number | null {
		if (v === '' || v === null || v === undefined) return null;
		const n = parseFloat(String(v));
		return Number.isFinite(n) ? n : null;
	}

	function buildPayload() {
		return {
			contract_number: contract_number.trim(),
			vendor_id,
			title: title.trim() || null,
			description: description.trim() || null,
			contract_type,
			currency: currency.trim() || 'USD',
			total_value,
			spend_limit,
			not_to_exceed,
			start_date: start_date || null,
			end_date: end_date || null,
			signed_date: signed_date || null,
			auto_renew,
			renewal_term_months,
			renewal_notice_days,
			payment_terms: payment_terms.trim() || null,
			line_items: lineItems.map((li, idx) => ({
				line_number: idx + 1,
				item_code: li.item_code,
				description: li.description,
				quantity: li.quantity,
				unit_price: li.unit_price,
				total: li.total,
				gl_account: li.gl_account
			}))
		};
	}

	// Surface the backend's 409 conflict text (illegal lifecycle transition,
	// delete of a non-draft, etc.) verbatim — it's user-actionable.
	function handleError(err: unknown, fallback: string) {
		toast(err instanceof Error ? err.message : fallback, 'error');
	}

	async function handleSave() {
		if (!contract_number.trim() || !vendor_id) return;
		saving = true;
		try {
			const saved = isCreate
				? await createContract(buildPayload())
				: await updateContract(contract!.id, buildPayload());
			toast(isCreate ? m('contracts.modal.toast.created') : m('contracts.modal.toast.saved'), 'success');
			onsaved(saved);
			onclose();
		} catch (err) {
			handleError(err, isCreate ? m('contracts.modal.toast.createFailed') : m('contracts.modal.toast.saveFailed'));
		} finally {
			saving = false;
		}
	}

	async function runLifecycle(fn: () => Promise<Contract>, successMsg: string, fallback: string) {
		busy = true;
		try {
			const updated = await fn();
			toast(successMsg, 'success');
			onsaved(updated);
		} catch (err) {
			handleError(err, fallback);
		} finally {
			busy = false;
		}
	}

	// --- Document upload ---
	let fileInput = $state<HTMLInputElement>();
	let uploading = $state(false);

	async function handleUpload(e: Event) {
		const input = e.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file || !contract) return;
		uploading = true;
		try {
			const updated = await uploadContractFile(contract.id, file);
			toast(m('contracts.modal.toast.uploaded'), 'success');
			onsaved(updated);
		} catch (err) {
			handleError(err, m('contracts.modal.toast.uploadFailed'));
		} finally {
			uploading = false;
			input.value = '';
		}
	}

	async function downloadDocument() {
		if (!contract?.file_key) return;
		try {
			const url = await fetchContractFile(contract.file_key);
			window.open(url, '_blank');
			// Revoke shortly after the new tab has had a chance to load it.
			setTimeout(() => URL.revokeObjectURL(url), 60_000);
		} catch (err) {
			handleError(err, m('contracts.modal.toast.docLoadFailed'));
		}
	}

	// --- Renew form ---
	let showRenew = $state(false);
	let renewEndDate = $state('');
	let renewTotalValue = $state<number | null>(null);
	let renewSpendLimit = $state<number | null>(null);

	async function handleRenew() {
		if (!contract || !renewEndDate) return;
		busy = true;
		try {
			const updated = await renewContract(contract.id, {
				end_date: renewEndDate,
				total_value: renewTotalValue,
				spend_limit: renewSpendLimit
			});
			toast(m('contracts.modal.toast.renewed'), 'success');
			showRenew = false;
			onsaved(updated);
		} catch (err) {
			handleError(err, m('contracts.modal.toast.renewFailed'));
		} finally {
			busy = false;
		}
	}

	// --- Create PO form ---
	let showCreatePo = $state(false);
	let poNumber = $state('');
	let poTotal = $state<number | null>(null);

	async function handleCreatePo() {
		if (!contract) return;
		busy = true;
		try {
			const po = await createPoFromContract(contract.id, {
				po_number: poNumber.trim() || undefined,
				total: poTotal
			});
			toast(m('contracts.modal.toast.poCreated', { number: po.po_number }), 'success');
			showCreatePo = false;
			poNumber = '';
			poTotal = null;
		} catch (err) {
			handleError(err, m('contracts.modal.toast.poCreateFailed'));
		} finally {
			busy = false;
		}
	}

	const modalTitle = $derived(
		isCreate
			? m('contracts.modal.title.new')
			: canEdit
				? m('contracts.modal.title.edit', { number: contract!.contract_number })
				: m('contracts.modal.title.view', { number: contract!.contract_number })
	);
	const ariaLabel = $derived(isCreate ? m('contracts.modal.aria.new') : m('contracts.modal.aria.detail'));
</script>

<Modal open {ariaLabel} title={modalTitle} width="lg" {onclose}>
	<form onsubmit={(e) => { e.preventDefault(); handleSave(); }}>
		{#if !isCreate}
			<div class="status-row">
				<span class="badge {status}">{STATUS_LABELS[status]}</span>
				{#if contract!.auto_renew}<span class="meta-pill">{m('contracts.modal.autoRenewPill')}</span>{/if}
			</div>
		{/if}

		<div class="form-grid">
			<label>
				<span>{m('contracts.modal.field.contractNumber')} <em class="required">*</em></span>
				<input type="text" bind:value={contract_number} required disabled={!canEdit} />
			</label>
			<label>
				<span>{m('contracts.modal.field.vendor')} <em class="required">*</em></span>
				<select bind:value={vendor_id} required disabled={!canEdit || !isCreate}>
					<option value="">{m('contracts.modal.field.vendorSelect')}</option>
					{#each vendors as v (v.id)}
						<option value={v.id}>{v.name}</option>
					{/each}
				</select>
			</label>
			<label class="full-width">
				<span>{m('contracts.modal.field.title')}</span>
				<input type="text" bind:value={title} disabled={!canEdit} />
			</label>
			<label>
				<span>{m('contracts.modal.field.type')}</span>
				<select bind:value={contract_type} disabled={!canEdit}>
					{#each CONTRACT_TYPES as t}
						<option value={t}>{CONTRACT_TYPE_LABELS[t]}</option>
					{/each}
				</select>
			</label>
			<label>
				<span>{m('contracts.modal.field.currency')}</span>
				<input type="text" bind:value={currency} maxlength="3" disabled={!canEdit} />
			</label>
			<label>
				<span>{m('contracts.modal.field.totalValue')}</span>
				<input
					type="number"
					step="0.01"
					min="0"
					value={total_value ?? ''}
					oninput={(e) => (total_value = numOrNull(e.currentTarget.value))}
					disabled={!canEdit}
				/>
			</label>
			<label>
				<span>{m('contracts.modal.field.spendLimit')}</span>
				<input
					type="number"
					step="0.01"
					min="0"
					value={spend_limit ?? ''}
					oninput={(e) => (spend_limit = numOrNull(e.currentTarget.value))}
					disabled={!canEdit}
				/>
			</label>
			<label class="checkbox-label">
				<input type="checkbox" bind:checked={not_to_exceed} disabled={!canEdit} />
				<span>{m('contracts.modal.field.notToExceed')}</span>
			</label>
			<label>
				<span>{m('contracts.modal.field.paymentTerms')}</span>
				<input type="text" bind:value={payment_terms} placeholder={m('contracts.modal.field.paymentTermsPlaceholder')} disabled={!canEdit} />
			</label>
			<label>
				<span>{m('contracts.modal.field.startDate')}</span>
				<input type="date" bind:value={start_date} disabled={!canEdit} />
			</label>
			<label>
				<span>{m('contracts.modal.field.endDate')}</span>
				<input type="date" bind:value={end_date} disabled={!canEdit} />
			</label>
			<label>
				<span>{m('contracts.modal.field.signedDate')}</span>
				<input type="date" bind:value={signed_date} disabled={!canEdit} />
			</label>
			<label class="checkbox-label">
				<input type="checkbox" bind:checked={auto_renew} disabled={!canEdit} />
				<span>{m('contracts.modal.field.autoRenew')}</span>
			</label>
			<label>
				<span>{m('contracts.modal.field.renewalTerm')}</span>
				<input
					type="number"
					min="1"
					value={renewal_term_months ?? ''}
					oninput={(e) => (renewal_term_months = numOrNull(e.currentTarget.value))}
					disabled={!canEdit}
				/>
			</label>
			<label>
				<span>{m('contracts.modal.field.renewalNotice')}</span>
				<input type="number" min="0" bind:value={renewal_notice_days} disabled={!canEdit} />
			</label>
			<label class="full-width">
				<span>{m('contracts.modal.field.description')}</span>
				<textarea bind:value={description} rows="2" disabled={!canEdit}></textarea>
			</label>
		</div>

		<!-- Spend summary (detail mode only) -->
		{#if spend}
			<div class="spend-panel" class:over={spend.over_limit}>
				<div class="spend-title">{m('contracts.modal.spend.title')}</div>
				<div class="spend-grid">
					<div>
						<span class="spend-label">{m('contracts.modal.spend.invoiced')}</span>
						<span class="spend-value"><Money amount={spend.invoiced_total} currency={currency} mono /></span>
					</div>
					<div>
						<span class="spend-label">{m('contracts.modal.spend.invoices')}</span>
						<span class="spend-value">{spend.invoice_count}</span>
					</div>
					{#if spend.spend_limit !== null}
						<div>
							<span class="spend-label">{m('contracts.modal.spend.limit')}</span>
							<span class="spend-value"><Money amount={spend.spend_limit} currency={currency} mono /></span>
						</div>
						<div>
							<span class="spend-label">{m('contracts.modal.spend.remaining')}</span>
							<span class="spend-value" class:neg={spend.over_limit}>
								<Money amount={spend.remaining} currency={currency} mono accounting />
							</span>
						</div>
					{/if}
				</div>
				{#if spend.over_limit}
					<div class="spend-warning">{m('contracts.modal.spend.overLimit')}</div>
				{/if}
			</div>
		{/if}

		<!-- Line items -->
		<div class="line-items-section">
			<div class="line-items-header">
				<span class="line-items-title">{m('contracts.modal.lineItems')}</span>
				{#if canEdit}
					<button type="button" class="btn-add-line" onclick={addLine}>{m('contracts.modal.addLine')}</button>
				{/if}
			</div>
			{#if lineItems.length > 0}
				<table class="line-items-table">
					<thead>
						<tr>
							<th>{m('contracts.modal.col.num')}</th>
							<th>{m('contracts.modal.col.description')}</th>
							<th class="right">{m('contracts.modal.col.qty')}</th>
							<th class="right">{m('contracts.modal.col.unitPrice')}</th>
							<th class="right">{m('contracts.modal.col.total')}</th>
							<th>{m('contracts.modal.col.gl')}</th>
							{#if canEdit}<th></th>{/if}
						</tr>
					</thead>
					<tbody>
						{#each lineItems as li, idx (idx)}
							<tr>
								<td class="li-num">{idx + 1}</td>
								<td><input type="text" class="li-input" aria-label={m('contracts.modal.line.descriptionAria', { n: idx + 1 })} value={li.description ?? ''} oninput={(e) => updateLine(idx, 'description', e.currentTarget.value)} disabled={!canEdit} /></td>
								<td><input type="number" class="li-input right" step="0.01" aria-label={m('contracts.modal.line.quantityAria', { n: idx + 1 })} value={li.quantity ?? ''} oninput={(e) => updateLine(idx, 'quantity', numOrNull(e.currentTarget.value))} disabled={!canEdit} /></td>
								<td><input type="number" class="li-input right" step="0.01" aria-label={m('contracts.modal.line.unitPriceAria', { n: idx + 1 })} value={li.unit_price ?? ''} oninput={(e) => updateLine(idx, 'unit_price', numOrNull(e.currentTarget.value))} disabled={!canEdit} /></td>
								<td><input type="number" class="li-input right" step="0.01" aria-label={m('contracts.modal.line.totalAria', { n: idx + 1 })} value={li.total ?? ''} oninput={(e) => updateLine(idx, 'total', numOrNull(e.currentTarget.value))} disabled={!canEdit} /></td>
								<td><input type="text" class="li-input li-gl" aria-label={m('contracts.modal.line.glAria', { n: idx + 1 })} value={li.gl_account ?? ''} oninput={(e) => updateLine(idx, 'gl_account', e.currentTarget.value)} disabled={!canEdit} /></td>
								{#if canEdit}
									<td><button type="button" class="li-delete" aria-label={m('contracts.modal.line.removeAria', { n: idx + 1 })} onclick={() => removeLine(idx)}>&times;</button></td>
								{/if}
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="line-items-empty">{m('contracts.modal.noLineItems')}</p>
			{/if}
		</div>

		<!-- Document -->
		{#if !isCreate}
			<div class="document-section">
				<span class="document-title">{m('contracts.modal.document.title')}</span>
				{#if contract!.file_key}
					<button type="button" class="btn-doc" onclick={downloadDocument}>{m('contracts.modal.document.view')}</button>
				{:else}
					<span class="document-empty">{m('contracts.modal.document.empty')}</span>
				{/if}
				{#if canEdit}
					<input type="file" accept=".pdf,.png,.jpg,.jpeg,.tiff,.doc,.docx" bind:this={fileInput} onchange={handleUpload} hidden />
					<button type="button" class="btn-doc-upload" disabled={uploading} onclick={() => fileInput?.click()}>
						{uploading ? m('contracts.modal.document.uploading') : contract!.file_key ? m('contracts.modal.document.replace') : m('contracts.modal.document.upload')}
					</button>
				{/if}
			</div>
		{/if}

		<!-- Renew sub-form -->
		{#if showRenew}
			<div class="sub-form">
				<div class="sub-form-title">{m('contracts.modal.renew.title')}</div>
				<div class="sub-form-grid">
					<label>
						<span>{m('contracts.modal.renew.newEndDate')} <em class="required">*</em></span>
						<input type="date" bind:value={renewEndDate} required />
					</label>
					<label>
						<span>{m('contracts.modal.renew.totalValue')}</span>
						<input type="number" step="0.01" min="0" value={renewTotalValue ?? ''} oninput={(e) => (renewTotalValue = numOrNull(e.currentTarget.value))} />
					</label>
					<label>
						<span>{m('contracts.modal.renew.spendLimit')}</span>
						<input type="number" step="0.01" min="0" value={renewSpendLimit ?? ''} oninput={(e) => (renewSpendLimit = numOrNull(e.currentTarget.value))} />
					</label>
				</div>
				<div class="sub-form-actions">
					<button type="button" class="btn-cancel-sm" onclick={() => (showRenew = false)}>{m('contracts.modal.renew.cancel')}</button>
					<button type="button" class="btn-primary-sm" disabled={busy || !renewEndDate} onclick={handleRenew}>
						{busy ? m('contracts.modal.renew.renewing') : m('contracts.modal.renew.confirm')}
					</button>
				</div>
			</div>
		{/if}

		<!-- Create PO sub-form -->
		{#if showCreatePo}
			<div class="sub-form">
				<div class="sub-form-title">{m('contracts.modal.createPo.title')}</div>
				<div class="sub-form-grid">
					<label>
						<span>{m('contracts.modal.createPo.poNumber')}</span>
						<input type="text" bind:value={poNumber} placeholder={m('contracts.modal.createPo.poNumberPlaceholder')} />
					</label>
					<label>
						<span>{m('contracts.modal.createPo.total')}</span>
						<input type="number" step="0.01" min="0" value={poTotal ?? ''} oninput={(e) => (poTotal = numOrNull(e.currentTarget.value))} />
					</label>
				</div>
				<div class="sub-form-actions">
					<button type="button" class="btn-cancel-sm" onclick={() => (showCreatePo = false)}>{m('contracts.modal.createPo.cancel')}</button>
					<button type="button" class="btn-primary-sm" disabled={busy} onclick={handleCreatePo}>
						{busy ? m('contracts.modal.createPo.creating') : m('contracts.modal.createPo.confirm')}
					</button>
				</div>
			</div>
		{/if}

		<!-- Lifecycle actions (detail mode, admin/ap_manager only) -->
		{#if !isCreate && canEdit}
			<div class="lifecycle-actions">
				{#if canActivate}
					<button type="button" class="btn-lifecycle activate" disabled={busy} onclick={() => runLifecycle(() => activateContract(contract!.id), m('contracts.modal.toast.activated'), m('contracts.modal.toast.activateFailed'))}>{m('contracts.modal.lifecycle.activate')}</button>
				{/if}
				{#if canTerminate}
					<button type="button" class="btn-lifecycle terminate" disabled={busy} onclick={() => runLifecycle(() => terminateContract(contract!.id), m('contracts.modal.toast.terminated'), m('contracts.modal.toast.terminateFailed'))}>{m('contracts.modal.lifecycle.terminate')}</button>
				{/if}
				{#if canCancel}
					<button type="button" class="btn-lifecycle cancel" disabled={busy} onclick={() => runLifecycle(() => cancelContract(contract!.id), m('contracts.modal.toast.cancelled'), m('contracts.modal.toast.cancelFailed'))}>{m('contracts.modal.lifecycle.cancel')}</button>
				{/if}
				{#if canRenew}
					<button type="button" class="btn-lifecycle" disabled={busy} onclick={() => { showRenew = !showRenew; showCreatePo = false; renewEndDate = end_date || ''; }}>{m('contracts.modal.lifecycle.renew')}</button>
				{/if}
				{#if canCreatePo}
					<button type="button" class="btn-lifecycle" disabled={busy} onclick={() => { showCreatePo = !showCreatePo; showRenew = false; }}>{m('contracts.modal.lifecycle.createPo')}</button>
				{/if}
			</div>
		{/if}

		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={onclose}>{m('contracts.modal.close')}</button>
			{#if canEdit}
				<button type="submit" class="btn-primary" disabled={saving}>
					{saving ? m('contracts.modal.saving') : isCreate ? m('contracts.modal.create') : m('contracts.modal.save')}
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
	.badge.active { background: rgba(31, 168, 106, 0.15); color: #1fa86a; }
	.badge.expired { background: rgba(212, 148, 10, 0.15); color: #d4940a; }
	.badge.terminated { background: rgba(224, 64, 64, 0.15); color: var(--danger); }
	.badge.cancelled { background: var(--bg); color: var(--text-muted); }

	.meta-pill {
		font-size: 0.72rem;
		padding: 2px 8px;
		border-radius: 8px;
		background: var(--bg);
		color: var(--text-muted);
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

	/* --- Spend panel --- */
	.spend-panel {
		margin-top: 16px;
		padding: 12px 14px;
		border: 1px solid var(--border);
		border-radius: 8px;
		background: var(--bg);
	}
	.spend-panel.over {
		border-color: var(--danger);
	}
	.spend-title {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text);
		margin-bottom: 8px;
	}
	.spend-grid {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 10px;
	}
	.spend-grid > div {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.spend-label {
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}
	.spend-value {
		font-size: 0.9rem;
		font-weight: 600;
		color: var(--text);
	}
	.spend-value.neg {
		color: var(--danger);
	}
	.spend-warning {
		margin-top: 8px;
		font-size: 0.78rem;
		font-weight: 600;
		color: var(--danger);
	}

	/* --- Line items --- */
	.line-items-section {
		margin-top: 16px;
	}
	.line-items-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 8px;
	}
	.line-items-title {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text);
	}
	.btn-add-line {
		padding: 4px 10px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.78rem;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-add-line:hover {
		border-color: var(--accent);
		color: var(--accent);
	}
	.line-items-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.82rem;
	}
	.line-items-table th {
		text-align: left;
		padding: 4px 6px;
		color: var(--text-muted);
		font-weight: 500;
		border-bottom: 1px solid var(--border);
	}
	.line-items-table th.right { text-align: right; }
	.line-items-table td {
		padding: 3px 4px;
	}
	.li-num {
		color: var(--text-muted);
		width: 24px;
	}
	.li-input {
		width: 100%;
		padding: 5px 7px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.82rem;
	}
	.li-input.right { text-align: right; }
	.li-input.li-gl { width: 80px; }
	.li-input:disabled { opacity: 0.7; }
	.li-delete {
		border: none;
		background: none;
		color: var(--text-muted);
		font-size: 1.1rem;
		cursor: pointer;
		line-height: 1;
	}
	.li-delete:hover { color: var(--danger); }
	.line-items-empty {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0;
	}

	/* --- Document --- */
	.document-section {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-top: 16px;
		flex-wrap: wrap;
	}
	.document-title {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text);
	}
	.document-empty {
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
	.btn-doc-upload:disabled { opacity: 0.6; cursor: not-allowed; }

	/* --- Sub-forms (renew / create-po) --- */
	.sub-form {
		margin-top: 16px;
		padding: 12px 14px;
		border: 1px solid var(--accent);
		border-radius: 8px;
		background: var(--bg);
	}
	.sub-form-title {
		font-size: 0.8rem;
		font-weight: 600;
		margin-bottom: 8px;
		color: var(--text);
	}
	.sub-form-grid {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 10px;
	}
	.sub-form-grid label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.78rem;
		color: var(--text-muted);
	}
	.sub-form-grid input {
		padding: 6px 8px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-family: inherit;
		font-size: 0.85rem;
	}
	.sub-form-actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 10px;
	}
	.btn-cancel-sm {
		padding: 5px 12px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-primary-sm {
		padding: 5px 12px;
		border-radius: 5px;
		border: none;
		background: var(--accent-strong);
		color: #fff;
		font-size: 0.8rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-primary-sm:disabled { opacity: 0.6; cursor: not-allowed; }

	/* --- Lifecycle actions --- */
	.lifecycle-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin-top: 16px;
		padding-top: 12px;
		border-top: 1px solid var(--border);
	}
	.btn-lifecycle {
		padding: 6px 14px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		transition: all 0.15s;
	}
	.btn-lifecycle:hover { border-color: var(--accent); color: var(--accent); }
	.btn-lifecycle:disabled { opacity: 0.6; cursor: not-allowed; }
	.btn-lifecycle.activate:hover { border-color: #1fa86a; color: #1fa86a; }
	.btn-lifecycle.terminate:hover,
	.btn-lifecycle.cancel:hover { border-color: var(--danger); color: var(--danger); }
</style>
