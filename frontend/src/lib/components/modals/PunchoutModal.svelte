<script lang="ts">
	import type { Catalog, PunchoutSession } from '$lib/types/catalog';
	import { PUNCHOUT_STATUS_LABELS } from '$lib/types/catalog';
	import { auth } from '$lib/stores/auth.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import {
		startPunchout,
		getPunchoutSession,
		convertPunchoutSession
	} from '$lib/api/catalogs';

	let {
		catalog,
		onclose,
		onconverted
	}: {
		catalog: Catalog;
		onclose: () => void;
		// Called with the new requisition id once the cart is converted.
		onconverted: (requisitionId: string) => void;
	} = $props();

	// Buyers (admin / ap_manager / ap_clerk) may start + convert; cfo is read-only.
	const canAct = $derived(auth.hasAnyRole('admin', 'ap_manager', 'ap_clerk'));

	let session = $state<PunchoutSession | null>(null);
	let starting = $state(false);
	let refreshing = $state(false);
	let converting = $state(false);

	const returned = $derived(session?.status === 'returned');
	const converted = $derived(session?.status === 'converted');

	async function start() {
		starting = true;
		try {
			const res = await startPunchout(catalog.id);
			// Pull the full session so the modal can render the cart once it returns.
			session = await getPunchoutSession(res.session_id);
			// Open the supplier start page in a new tab — the buyer shops there.
			if (res.start_url) window.open(res.start_url, '_blank', 'noopener');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Could not start punch-out', 'error');
		} finally {
			starting = false;
		}
	}

	async function refresh() {
		if (!session) return;
		refreshing = true;
		try {
			session = await getPunchoutSession(session.id);
			if (session.status !== 'returned' && session.status !== 'converted') {
				toast('No cart returned yet — finish shopping at the supplier, then refresh.', 'info');
			}
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Could not refresh session', 'error');
		} finally {
			refreshing = false;
		}
	}

	async function convert() {
		if (!session) return;
		converting = true;
		try {
			const res = await convertPunchoutSession(session.id);
			session = await getPunchoutSession(session.id);
			toast(
				res.created
					? `Created requisition ${res.requisition_number}`
					: `Requisition ${res.requisition_number} already created`,
				'success'
			);
			onconverted(res.requisition_id);
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Convert failed', 'error');
		} finally {
			converting = false;
		}
	}
</script>

<Modal open ariaLabel="Punch-out" title={`Punch-out — ${catalog.name}`} width="md" {onclose}>
	<div class="punchout-body">
		{#if !session}
			<p class="muted">
				Start a punch-out session to shop the supplier's hosted catalog. The supplier returns your
				cart here, and you convert it into a requisition.
			</p>
			{#if canAct}
				<button class="btn-primary" onclick={start} disabled={starting}>
					{starting ? 'Starting…' : 'Start punch-out'}
				</button>
			{:else}
				<p class="muted">Your role can't start a punch-out session.</p>
			{/if}
		{:else}
			<div class="status-row">
				<span class="label">Status</span>
				<span class="status status-{session.status}">
					{PUNCHOUT_STATUS_LABELS[session.status] ?? session.status}
				</span>
			</div>

			{#if session.start_url && !converted}
				<p class="muted">
					Shopping at the supplier?
					<a href={session.start_url} target="_blank" rel="noopener">Re-open supplier catalog</a>.
				</p>
			{/if}

			{#if !returned && !converted}
				<button class="btn-secondary" onclick={refresh} disabled={refreshing}>
					{refreshing ? 'Checking…' : 'Refresh — has the cart returned?'}
				</button>
			{/if}

			{#if (returned || converted) && session.cart_items.length > 0}
				<table class="cart">
					<thead>
						<tr>
							<th>Item</th>
							<th class="right">Qty</th>
							<th class="right">Unit price</th>
						</tr>
					</thead>
					<tbody>
						{#each session.cart_items as it, i (i)}
							<tr>
								<td>{it.description}{#if it.sku}<span class="sku"> · {it.sku}</span>{/if}</td>
								<td class="right">{it.quantity ?? '—'}</td>
								<td class="right">
									{#if it.unit_price != null}
										<Money amount={it.unit_price} currency={it.currency} />
									{:else}—{/if}
								</td>
							</tr>
						{/each}
					</tbody>
					<tfoot>
						<tr>
							<td colspan="2" class="right"><strong>Cart total</strong></td>
							<td class="right">
								<strong><Money amount={session.cart_total ?? 0} currency={session.currency} /></strong>
							</td>
						</tr>
					</tfoot>
				</table>
			{/if}

			{#if converted && session.converted_requisition_id}
				<p class="muted">Converted to a requisition.</p>
			{/if}
		{/if}

		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={onclose}>Close</button>
			{#if returned && canAct}
				<button type="button" class="btn-primary" onclick={convert} disabled={converting}>
					{converting ? 'Converting…' : 'Convert to requisition'}
				</button>
			{/if}
		</div>
	</div>
</Modal>

<style>
	.punchout-body {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.muted {
		color: var(--text-muted);
		font-size: 0.86rem;
		margin: 0;
	}
	.status-row {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.label {
		font-size: 0.78rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--text-muted);
	}
	.status {
		display: inline-block;
		padding: 2px 9px;
		border-radius: 10px;
		font-size: 0.78rem;
		font-weight: 600;
		background: var(--surface-2, rgba(127, 127, 127, 0.15));
	}
	.status-returned {
		background: rgba(31, 168, 106, 0.15);
		color: #1fa86a;
	}
	.status-converted {
		background: rgba(63, 124, 240, 0.15);
		color: #3f7cf0;
	}
	.cart {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.86rem;
	}
	.cart th,
	.cart td {
		padding: 6px 8px;
		border-bottom: 1px solid var(--border);
		text-align: left;
	}
	.cart .right {
		text-align: right;
	}
	.cart .sku {
		color: var(--text-muted);
	}
</style>
