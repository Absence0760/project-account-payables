<script lang="ts">
	import type { Invoice } from '$lib/types/invoice';
	import { INVOICE_STATUSES, STATUS_LABELS } from '$lib/types/invoice';
	import { invoiceStore } from '$lib/stores/invoices.svelte';

	let {
		invoice,
		onclose,
	}: {
		invoice: Invoice;
		onclose: () => void;
	} = $props();

	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot, intentional */
	let vendor = $state(invoice.vendor);
	let invoice_number = $state(invoice.invoice_number);
	let amount = $state(invoice.amount);
	let due_date = $state(invoice.due_date);
	let status = $state(invoice.status);
	let po_number = $state(invoice.po_number);
	let description = $state(invoice.description);
	/* eslint-enable svelte/state-referenced-locally */

	let fullscreen = $state(false);

	function toggleFullscreen() {
		fullscreen = !fullscreen;
	}

	let saving = $state(false);
	let error = $state('');

	async function save() {
		saving = true;
		error = '';
		try {
			await invoiceStore.update(invoice.id, {
				vendor,
				invoice_number,
				amount,
				due_date,
				status,
				po_number,
				description,
			});
			onclose();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Save failed';
		} finally {
			saving = false;
		}
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
	<div class="modal" class:fullscreen role="dialog" aria-label="Edit invoice {invoice.invoice_number}">
		<header>
			<h2>Edit Invoice &mdash; {invoice.invoice_number}</h2>
			<div class="header-actions">
				<button class="icon-btn" onclick={toggleFullscreen} aria-label={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}>
					{#if fullscreen}
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
							<polyline points="4 14 10 14 10 20" /><polyline points="20 10 14 10 14 4" />
							<line x1="14" y1="10" x2="21" y2="3" /><line x1="3" y1="21" x2="10" y2="14" />
						</svg>
					{:else}
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
							<polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" />
							<line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" />
						</svg>
					{/if}
				</button>
				<button class="icon-btn close-btn" onclick={onclose} aria-label="Close">&times;</button>
			</div>
		</header>

		<div class="split">
			<div class="pdf-pane">
				{#if invoice.file_url}
					<iframe src={invoice.file_url} title="Invoice PDF — {invoice.invoice_number}"></iframe>
				{:else}
					<div class="no-pdf">
						<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
							<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
							<polyline points="14 2 14 8 20 8" />
							<line x1="9" y1="15" x2="15" y2="15" />
						</svg>
						<span>No PDF attached</span>
					</div>
				{/if}
			</div>

			<div class="form-pane">
				<form onsubmit={(e) => { e.preventDefault(); save(); }}>
					<div class="form-grid">
						<label>
							<span>Vendor</span>
							<input type="text" bind:value={vendor} />
						</label>
						<label>
							<span>Invoice #</span>
							<input type="text" bind:value={invoice_number} />
						</label>
						<label>
							<span>Amount</span>
							<input type="number" step="0.01" bind:value={amount} />
						</label>
						<label>
							<span>Due Date</span>
							<input type="date" bind:value={due_date} />
						</label>
						<label>
							<span>PO Number</span>
							<input type="text" bind:value={po_number} />
						</label>
						<label>
							<span>Status</span>
							<select bind:value={status}>
								{#each INVOICE_STATUSES as s}
									<option value={s}>{STATUS_LABELS[s]}</option>
								{/each}
							</select>
						</label>
						<label class="full-width">
							<span>Description</span>
							<input type="text" bind:value={description} />
						</label>
					</div>

					{#if error}
						<div class="save-error">{error}</div>
					{/if}

					<footer>
						<button type="button" class="btn-cancel" onclick={onclose}>Cancel</button>
						<button type="submit" class="btn-save" disabled={saving}>
							{saving ? 'Saving...' : 'Save'}
						</button>
					</footer>
				</form>
			</div>
		</div>
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
		width: min(1100px, 95vw);
		height: min(720px, 90vh);
		display: flex;
		flex-direction: column;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.3);
		overflow: hidden;
		transition: all 0.2s ease;
	}

	.modal.fullscreen {
		width: 100vw;
		height: 100vh;
		border-radius: 0;
		border: none;
	}

	header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 14px 20px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	h2 {
		margin: 0;
		font-size: 1.05rem;
		font-weight: 600;
	}

	.header-actions {
		display: flex;
		align-items: center;
		gap: 4px;
	}

	.icon-btn {
		background: none;
		border: 1px solid transparent;
		border-radius: 4px;
		cursor: pointer;
		color: var(--text-muted);
		padding: 6px;
		display: grid;
		place-items: center;
	}

	.icon-btn:hover {
		color: var(--text);
		background: var(--bg);
		border-color: var(--border);
	}

	.close-btn {
		font-size: 1.4rem;
		line-height: 1;
		padding: 4px 6px;
	}

	/* --- Split pane --- */

	.split {
		display: flex;
		flex: 1;
		min-height: 0;
	}

	/* --- PDF pane --- */

	.pdf-pane {
		flex: 1;
		border-right: 1px solid var(--border);
		background: #1a1a24;
		display: flex;
	}

	.pdf-pane iframe {
		width: 100%;
		height: 100%;
		border: none;
	}

	.no-pdf {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 12px;
		color: var(--text-muted);
		font-size: 0.9rem;
	}

	/* --- Form pane --- */

	.form-pane {
		width: 380px;
		flex-shrink: 0;
		overflow-y: auto;
		display: flex;
		flex-direction: column;
	}

	form {
		padding: 20px;
		display: flex;
		flex-direction: column;
		flex: 1;
	}

	.form-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 14px;
	}

	.full-width {
		grid-column: 1 / -1;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	label span {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	input,
	select {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.88rem;
		color: var(--text);
		font-family: inherit;
	}

	input:focus,
	select:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	footer {
		display: flex;
		justify-content: flex-end;
		gap: 10px;
		padding-top: 18px;
		border-top: 1px solid var(--border);
		margin-top: auto;
		padding-top: 18px;
	}

	.btn-cancel,
	.btn-save {
		padding: 8px 18px;
		border-radius: 4px;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		border: 1px solid var(--border);
	}

	.btn-cancel {
		background: var(--surface);
		color: var(--text-muted);
	}

	.btn-cancel:hover {
		background: var(--bg);
	}

	.btn-save {
		background: var(--accent);
		color: #fff;
		border-color: var(--accent);
	}

	.btn-save:hover:not(:disabled) {
		opacity: 0.9;
	}

	.btn-save:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.save-error {
		background: rgba(224, 64, 64, 0.1);
		border: 1px solid rgba(224, 64, 64, 0.3);
		color: #e04040;
		padding: 8px 12px;
		border-radius: 4px;
		font-size: 0.82rem;
		margin-top: 8px;
	}

	/* --- Responsive: stack on narrow screens --- */

	@media (max-width: 768px) {
		.split {
			flex-direction: column;
		}

		.pdf-pane {
			border-right: none;
			border-bottom: 1px solid var(--border);
			height: 45%;
			flex: none;
		}

		.form-pane {
			width: 100%;
		}
	}
</style>
