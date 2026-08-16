<script lang="ts">
	import type { IntakeRequest, IntakeType } from '$lib/types/intake';
	import {
		INTAKE_TYPES,
		INTAKE_TYPE_LABELS,
		INTAKE_STATUS_LABELS,
		INTAKE_FORM_FIELDS
	} from '$lib/types/intake';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import { createIntake, updateIntake } from '$lib/api/intake';

	let {
		intake,
		onclose,
		onsaved
	}: {
		// null → create mode; an IntakeRequest → detail/edit mode.
		intake: IntakeRequest | null;
		onclose: () => void;
		onsaved: (i: IntakeRequest) => void;
	} = $props();

	const isCreate = $derived(intake === null);
	// Anyone in the org can raise / edit their own open intake.
	const canCreate = $derived(auth.hasAnyRole('admin', 'ap_manager', 'ap_clerk', 'cfo'));
	// Edits are only allowed while the intake is `open`.
	const editable = $derived(isCreate || intake?.status === 'open');
	const canEdit = $derived(canCreate && editable);

	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot */
	let title = $state(intake?.title ?? '');
	let request_type = $state<IntakeType>((intake?.request_type as IntakeType) ?? 'software');
	let estimated_amount = $state<number | null>(intake?.estimated_amount ?? null);
	let currency = $state(intake?.currency ?? orgCurrency.currency);
	let vendor_name = $state(intake?.vendor_name ?? '');
	let needed_by = $state(intake?.needed_by ?? '');
	let description = $state(intake?.description ?? '');
	let justification = $state(intake?.justification ?? '');
	// Questionnaire answers keyed by field key — seeded from the existing row.
	let formData = $state<Record<string, string>>(
		Object.fromEntries(
			Object.entries((intake?.form_data ?? {}) as Record<string, unknown>).map(([k, v]) => [
				k,
				v == null ? '' : String(v)
			])
		)
	);
	/* eslint-enable svelte/state-referenced-locally */

	let saving = $state(false);

	const status = $derived(intake?.status ?? 'open');
	// The dynamic questionnaire fields for the currently-selected type.
	const fields = $derived(INTAKE_FORM_FIELDS[request_type] ?? INTAKE_FORM_FIELDS.other);

	function numOrNull(v: unknown): number | null {
		if (v === '' || v === null || v === undefined) return null;
		const n = parseFloat(String(v));
		return Number.isFinite(n) ? n : null;
	}

	function collectFormData(): Record<string, unknown> | null {
		const out: Record<string, unknown> = {};
		// Preserve any existing non-field keys (e.g. review_reason stamped on reject).
		for (const [k, v] of Object.entries((intake?.form_data ?? {}) as Record<string, unknown>)) {
			if (!fields.some((f) => f.key === k)) out[k] = v;
		}
		for (const f of fields) {
			const v = formData[f.key]?.trim();
			if (v) out[f.key] = v;
		}
		return Object.keys(out).length ? out : null;
	}

	async function handleSave() {
		if (!title.trim()) return;
		saving = true;
		try {
			const payload = {
				title: title.trim(),
				request_type,
				estimated_amount,
				currency: currency.trim() || 'USD',
				vendor_name: vendor_name.trim() || null,
				needed_by: needed_by || null,
				description: description.trim() || null,
				justification: justification.trim() || null,
				form_data: collectFormData()
			};
			const saved = isCreate
				? await createIntake(payload)
				: await updateIntake(intake!.id, payload);
			toast(isCreate ? m('intake.modal.toast.created') : m('intake.modal.toast.saved'), 'success');
			onsaved(saved);
			onclose();
		} catch (err) {
			toast(
				err instanceof Error
					? err.message
					: isCreate
						? m('intake.modal.toast.createFailed')
						: m('intake.modal.toast.saveFailed'),
				'error'
			);
		} finally {
			saving = false;
		}
	}

	const modalTitle = $derived(
		isCreate
			? m('intake.modal.title.new')
			: canEdit
				? m('intake.modal.title.edit', { title: intake!.title })
				: m('intake.modal.title.view', { title: intake!.title })
	);
	const ariaLabel = $derived(isCreate ? m('intake.modal.aria.new') : m('intake.modal.aria.detail'));
</script>

<Modal open {ariaLabel} title={modalTitle} width="lg" {onclose}>
	<form onsubmit={(e) => { e.preventDefault(); handleSave(); }}>
		{#if !isCreate}
			<div class="status-row">
				<span class="number">{intake!.request_number}</span>
				<span class="badge {status}">{INTAKE_STATUS_LABELS[status as keyof typeof INTAKE_STATUS_LABELS] ?? status}</span>
				{#if intake!.converted_requisition_id}
					<span class="converted-note">{m('intake.modal.requisitionCreatedNote')}</span>
				{/if}
			</div>
		{/if}

		<div class="form-grid">
			<label class="full-width">
				<span>{m('intake.modal.field.title')} <em class="required">*</em></span>
				<input type="text" bind:value={title} required disabled={!canEdit} />
			</label>
			<label>
				<span>{m('intake.modal.field.type')}</span>
				<select bind:value={request_type} disabled={!canEdit}>
					{#each INTAKE_TYPES as t}
						<option value={t}>{INTAKE_TYPE_LABELS[t]}</option>
					{/each}
				</select>
			</label>
			<label>
				<span>{m('intake.modal.field.estimatedAmount')}</span>
				<input
					type="number"
					step="0.01"
					min="0"
					value={estimated_amount ?? ''}
					oninput={(e) => (estimated_amount = numOrNull(e.currentTarget.value))}
					disabled={!canEdit}
				/>
			</label>
			<label>
				<span>{m('intake.modal.field.currency')}</span>
				<input type="text" bind:value={currency} maxlength="3" disabled={!canEdit} />
			</label>
			<label>
				<span>{m('intake.modal.field.vendor')}</span>
				<input type="text" bind:value={vendor_name} placeholder={m('intake.modal.field.vendorPlaceholder')} disabled={!canEdit} />
			</label>
			<label>
				<span>{m('intake.modal.field.neededBy')}</span>
				<input type="date" bind:value={needed_by} disabled={!canEdit} />
			</label>
			<label class="full-width">
				<span>{m('intake.modal.field.description')}</span>
				<textarea bind:value={description} rows="2" disabled={!canEdit}></textarea>
			</label>
			<label class="full-width">
				<span>{m('intake.modal.field.justification')}</span>
				<textarea bind:value={justification} rows="2" disabled={!canEdit}></textarea>
			</label>
		</div>

		<!-- Flexible questionnaire — fields vary by request type. -->
		<div class="questionnaire">
			<span class="section-title">{m('intake.modal.questionnaire', { type: INTAKE_TYPE_LABELS[request_type] })}</span>
			<div class="form-grid">
				{#each fields as f (f.key)}
					<label>
						<span>{f.label}</span>
						<input type="text" bind:value={formData[f.key]} disabled={!canEdit} />
					</label>
				{/each}
			</div>
		</div>

		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={onclose}>{m('intake.modal.close')}</button>
			{#if canEdit}
				<button type="submit" class="btn-primary" disabled={saving || !title.trim()}>
					{saving ? m('intake.modal.saving') : isCreate ? m('intake.modal.create') : m('intake.modal.save')}
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
	.number {
		font-family: var(--font-mono);
		font-size: 0.85rem;
		color: var(--text-muted);
	}
	.converted-note {
		font-size: 0.78rem;
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
	.badge.open { background: rgba(99, 140, 255, 0.15); color: #638cff; }
	.badge.in_review { background: rgba(212, 148, 10, 0.15); color: #d4940a; }
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

	.questionnaire {
		margin-top: 16px;
		border-top: 1px solid var(--border);
		padding-top: 14px;
	}
	.section-title {
		display: block;
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text);
		margin-bottom: 10px;
		text-transform: capitalize;
	}
</style>
