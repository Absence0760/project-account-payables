<script lang="ts">
	import { INVOICE_STATUSES, STATUS_LABELS } from '$lib/types/invoice';
	import type { AdvancedSearchFilters } from '$lib/types/invoice';

	let {
		filters,
		onclose,
		onapply,
	}: {
		filters: AdvancedSearchFilters;
		onclose: () => void;
		onapply: (filters: AdvancedSearchFilters) => void;
	} = $props();

	let vendor = $state(filters.vendor);
	let invoice_number = $state(filters.invoice_number);
	let po_number = $state(filters.po_number);
	let description = $state(filters.description);
	let amount_min = $state(filters.amount_min);
	let amount_max = $state(filters.amount_max);
	let due_date_from = $state(filters.due_date_from);
	let due_date_to = $state(filters.due_date_to);
	let statuses = $state<string[]>([...filters.statuses]);

	function toggleStatus(s: string) {
		if (statuses.includes(s)) {
			statuses = statuses.filter((v) => v !== s);
		} else {
			statuses = [...statuses, s];
		}
	}

	function apply() {
		onapply({
			vendor,
			invoice_number,
			po_number,
			description,
			amount_min,
			amount_max,
			due_date_from,
			due_date_to,
			statuses,
		});
		onclose();
	}

	function clear() {
		vendor = '';
		invoice_number = '';
		po_number = '';
		description = '';
		amount_min = '';
		amount_max = '';
		due_date_from = '';
		due_date_to = '';
		statuses = [];
	}

	function handleBackdrop(e: MouseEvent) {
		if (e.target === e.currentTarget) onclose();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="backdrop" onclick={handleBackdrop}>
	<div class="modal" role="dialog" aria-label="Advanced search">
		<header>
			<h2>Advanced Search</h2>
			<button class="close-btn" onclick={onclose} aria-label="Close">&times;</button>
		</header>

		<form onsubmit={(e) => { e.preventDefault(); apply(); }}>
			<div class="form-grid">
				<label>
					<span>Vendor</span>
					<input type="text" placeholder="e.g. Acme Corp" bind:value={vendor} />
				</label>
				<label>
					<span>Invoice #</span>
					<input type="text" placeholder="e.g. INV-2024-001" bind:value={invoice_number} />
				</label>
				<label>
					<span>PO Number</span>
					<input type="text" placeholder="e.g. PO-1001" bind:value={po_number} />
				</label>
				<label>
					<span>Description</span>
					<input type="text" placeholder="Keywords..." bind:value={description} />
				</label>
				<label>
					<span>Amount Min</span>
					<input type="number" step="0.01" placeholder="0.00" bind:value={amount_min} />
				</label>
				<label>
					<span>Amount Max</span>
					<input type="number" step="0.01" placeholder="Any" bind:value={amount_max} />
				</label>
				<label>
					<span>Due Date From</span>
					<input type="date" bind:value={due_date_from} />
				</label>
				<label>
					<span>Due Date To</span>
					<input type="date" bind:value={due_date_to} />
				</label>
			</div>

			<fieldset>
				<legend>Status</legend>
				<div class="status-chips">
					{#each INVOICE_STATUSES as s}
						<button
							type="button"
							class="status-chip"
							class:selected={statuses.includes(s)}
							onclick={() => toggleStatus(s)}
						>
							{STATUS_LABELS[s]}
						</button>
					{/each}
				</div>
			</fieldset>

			<footer>
				<button type="button" class="btn-clear" onclick={clear}>Clear All</button>
				<div class="footer-right">
					<button type="button" class="btn-cancel" onclick={onclose}>Cancel</button>
					<button type="submit" class="btn-apply">Apply Filters</button>
				</div>
			</footer>
		</form>
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: grid;
		place-items: center;
		z-index: 100;
		backdrop-filter: blur(2px);
	}

	.modal {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		width: min(520px, 95vw);
		max-height: 90vh;
		overflow-y: auto;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.3);
	}

	header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 16px 20px;
		border-bottom: 1px solid var(--border);
	}

	h2 {
		margin: 0;
		font-size: 1.1rem;
		font-weight: 600;
	}

	.close-btn {
		background: none;
		border: none;
		font-size: 1.5rem;
		cursor: pointer;
		color: var(--text-muted);
		line-height: 1;
		padding: 0 4px;
	}

	.close-btn:hover {
		color: var(--text);
	}

	form {
		padding: 20px;
	}

	.form-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 14px;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}

	label span {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	input {
		width: 100%;
		min-width: 0;
		box-sizing: border-box;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.88rem;
		color: var(--text);
		font-family: inherit;
	}

	input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	input::placeholder {
		color: var(--text-muted);
		opacity: 0.6;
	}

	fieldset {
		border: none;
		margin: 18px 0 0;
		padding: 0;
	}

	legend {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
		margin-bottom: 8px;
	}

	.status-chips {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}

	.status-chip {
		padding: 5px 12px;
		border-radius: 16px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text-muted);
		font-size: 0.8rem;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.15s;
		font-family: inherit;
	}

	.status-chip:hover {
		border-color: var(--accent);
		color: var(--text);
	}

	.status-chip.selected {
		background: var(--accent);
		color: #fff;
		border-color: var(--accent);
	}

	footer {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding-top: 18px;
		border-top: 1px solid var(--border);
		margin-top: 20px;
	}

	.footer-right {
		display: flex;
		gap: 10px;
	}

	.btn-clear,
	.btn-cancel,
	.btn-apply {
		padding: 8px 18px;
		border-radius: 4px;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		border: 1px solid var(--border);
		font-family: inherit;
	}

	.btn-clear {
		background: none;
		color: var(--text-muted);
		border-color: transparent;
	}

	.btn-clear:hover {
		color: var(--text);
		text-decoration: underline;
	}

	.btn-cancel {
		background: var(--surface);
		color: var(--text-muted);
	}

	.btn-cancel:hover {
		background: var(--bg);
	}

	.btn-apply {
		background: var(--accent);
		color: #fff;
		border-color: var(--accent);
	}

	.btn-apply:hover {
		opacity: 0.9;
	}
</style>
