<script lang="ts">
	import type { Budget, BudgetDimension, BudgetSpend } from '$lib/types/budget';
	import { BUDGET_DIMENSIONS, BUDGET_DIMENSION_LABELS } from '$lib/types/budget';
	import { auth } from '$lib/stores/auth.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { createBudget, updateBudget, getBudgetSpend } from '$lib/api/budgets';

	let {
		budget,
		onclose,
		onsaved
	}: {
		// null → create mode; a Budget → detail/edit mode.
		budget: Budget | null;
		onclose: () => void;
		onsaved: (b: Budget) => void;
	} = $props();

	const isCreate = $derived(budget === null);
	// Budgets are financial config — only admin / cfo may mutate.
	const canEdit = $derived(auth.hasAnyRole('admin', 'cfo'));

	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot */
	let name = $state(budget?.name ?? '');
	let dimension = $state<BudgetDimension>((budget?.dimension as BudgetDimension) ?? 'department');
	let dimension_value = $state(budget?.dimension_value ?? '');
	let period = $state(budget?.period ?? '');
	let period_start = $state(budget?.period_start ?? '');
	let period_end = $state(budget?.period_end ?? '');
	let amount = $state<number | null>(budget?.amount ?? null);
	let currency = $state(budget?.currency ?? 'USD');
	let notes = $state(budget?.notes ?? '');
	/* eslint-enable svelte/state-referenced-locally */

	let saving = $state(false);

	// Spend rollup loaded lazily in detail mode.
	let spend = $state<BudgetSpend | null>(null);
	let spendLoading = $state(false);

	$effect(() => {
		if (budget) loadSpend(budget.id);
	});

	async function loadSpend(id: string) {
		spendLoading = true;
		try {
			spend = await getBudgetSpend(id);
		} catch {
			spend = null;
		} finally {
			spendLoading = false;
		}
	}

	function numOrNull(v: unknown): number | null {
		if (v === '' || v === null || v === undefined) return null;
		const n = parseFloat(String(v));
		return Number.isFinite(n) ? n : null;
	}

	function handleError(err: unknown, fallback: string) {
		toast(err instanceof Error ? err.message : fallback, 'error');
	}

	const utilClass = $derived(
		spend == null
			? ''
			: spend.utilization_pct >= 100
				? 'over'
				: spend.utilization_pct >= 80
					? 'warn'
					: 'ok'
	);

	async function handleSave() {
		if (!name.trim() || !dimension_value.trim() || amount == null) return;
		saving = true;
		try {
			const payload = {
				name: name.trim(),
				dimension,
				dimension_value: dimension_value.trim(),
				period: period.trim() || null,
				period_start: period_start || null,
				period_end: period_end || null,
				amount,
				currency: currency.trim() || 'USD',
				notes: notes.trim() || null
			};
			const saved = isCreate ? await createBudget(payload) : await updateBudget(budget!.id, payload);
			toast(isCreate ? 'Budget created' : 'Budget saved', 'success');
			onsaved(saved);
			onclose();
		} catch (err) {
			handleError(err, isCreate ? 'Create failed' : 'Save failed');
		} finally {
			saving = false;
		}
	}

	const modalTitle = $derived(
		isCreate ? 'New Budget' : canEdit ? `Edit Budget — ${budget!.name}` : `Budget — ${budget!.name}`
	);
	const ariaLabel = $derived(isCreate ? 'New budget' : 'Budget detail');
</script>

<Modal open {ariaLabel} title={modalTitle} width="lg" {onclose}>
	<form onsubmit={(e) => { e.preventDefault(); handleSave(); }}>
		{#if !isCreate}
			<!-- Spend rollup (computed on read) -->
			{#if spend}
				<div class="kpi-row">
					<KpiCard
						value={`${spend.utilization_pct.toFixed(1)}%`}
						label="Utilization"
						highlight={spend.utilization_pct >= 100 ? 'red' : null}
					/>
					<KpiCard value={formatMoney(spend.committed, { currency: spend.currency })} label="Committed" />
					<KpiCard
						value={formatMoney(spend.remaining, { currency: spend.currency })}
						label="Remaining"
						highlight={spend.remaining < 0 ? 'red' : 'green'}
					/>
				</div>
				<div class="util-bar" aria-label={`Budget utilization ${spend.utilization_pct.toFixed(1)} percent`}>
					<div class="util-fill {utilClass}" style={`width: ${Math.min(spend.utilization_pct, 100)}%`}></div>
				</div>
				<div class="spend-detail">
					<span>Allocated <Money amount={spend.allocated} currency={spend.currency} /></span>
					<span>Committed <Money amount={spend.committed} currency={spend.currency} /></span>
					<span>Actual <Money amount={spend.actual} currency={spend.currency} /></span>
					<span class:over={spend.remaining < 0}>
						Remaining <Money amount={spend.remaining} currency={spend.currency} accounting />
					</span>
				</div>
			{:else if spendLoading}
				<p class="muted">Loading spend…</p>
			{/if}
		{/if}

		<div class="form-grid">
			<label class="full-width">
				<span>Name <em class="required">*</em></span>
				<input type="text" bind:value={name} required disabled={!canEdit} />
			</label>
			<label>
				<span>Dimension</span>
				<select bind:value={dimension} disabled={!canEdit}>
					{#each BUDGET_DIMENSIONS as d}
						<option value={d}>{BUDGET_DIMENSION_LABELS[d]}</option>
					{/each}
				</select>
			</label>
			<label>
				<span>Dimension Value <em class="required">*</em></span>
				<input
					type="text"
					bind:value={dimension_value}
					placeholder="e.g. Engineering"
					required
					disabled={!canEdit}
				/>
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
				<span>Period</span>
				<input type="text" bind:value={period} placeholder="e.g. 2026 or 2026-Q2" disabled={!canEdit} />
			</label>
			<label>
				<span>Period Start</span>
				<input type="date" bind:value={period_start} disabled={!canEdit} />
			</label>
			<label>
				<span>Period End</span>
				<input type="date" bind:value={period_end} disabled={!canEdit} />
			</label>
			<label class="full-width">
				<span>Notes</span>
				<textarea bind:value={notes} rows="2" disabled={!canEdit}></textarea>
			</label>
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

	.util-bar {
		height: 8px;
		border-radius: 4px;
		background: var(--bg);
		border: 1px solid var(--border);
		overflow: hidden;
		margin: 4px 0 12px;
	}
	.util-fill {
		height: 100%;
		border-radius: 4px;
		transition: width 0.2s ease;
	}
	.util-fill.ok { background: #1fa86a; }
	.util-fill.warn { background: #d4940a; }
	.util-fill.over { background: #e04040; }

	.spend-detail {
		display: flex;
		flex-wrap: wrap;
		gap: 18px;
		margin-bottom: 16px;
		font-size: 0.8rem;
		color: var(--text-muted);
	}
	.spend-detail span.over {
		color: #e04040;
		font-weight: 600;
	}

	.muted {
		color: var(--text-muted);
		font-size: 0.85rem;
	}
</style>
