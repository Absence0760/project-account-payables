<script lang="ts">
	// Vendor detail modal — "Screening & Risk" panel for the Sanctions & Vendor
	// Risk Screening feature. Shows the current screening status, last-screened
	// time, payment-block state + reason, risk level/score, and the screening
	// history timeline. Mutating actions (re-screen, recompute risk, block,
	// unblock) are role-gated to admin / ap_manager via the auth store and emit
	// the updated vendor (or refreshed risk) back to the parent list.
	import { auth } from '$lib/stores/auth.svelte';
	import { PERM_VENDOR_BLOCK } from '$lib/types/admin';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import ScreeningBadge from '$lib/components/ui/ScreeningBadge.svelte';
	import {
		screenVendor,
		getScreeningHistory,
		blockVendor,
		unblockVendor,
		recomputeVendorRisk
	} from '$lib/api/vendors';
	import {
		RISK_LEVEL_LABELS,
		type Vendor,
		type SanctionsCheck,
		type ScreeningStatus
	} from '$lib/types/vendor';

	const SCREENING_RESULTS: ScreeningStatus[] = ['unscreened', 'clear', 'review', 'match'];

	// A history `result` may be a recognised screening status (render as a badge)
	// or a free-form provider string (render as plain text).
	function asScreeningStatus(result: string): ScreeningStatus | undefined {
		return (SCREENING_RESULTS as string[]).includes(result)
			? (result as ScreeningStatus)
			: undefined;
	}

	let {
		vendor,
		onclose,
		onupdated
	}: {
		vendor: Vendor;
		onclose: () => void;
		onupdated: (v: Vendor) => void;
	} = $props();

	const canMutate = $derived(auth.isManager); // admin | ap_manager (re-screen/recompute)
	// Block/unblock moved to the granular permission so an org can split it from
	// the rest of vendor management. Defaults to admin/ap_manager (unchanged).
	const canBlock = $derived(auth.can(PERM_VENDOR_BLOCK));

	let history = $state<SanctionsCheck[]>([]);
	let loadingHistory = $state(true);
	let busy = $state(''); // which action is in flight ('' = none)

	// Load history whenever the open vendor changes.
	$effect(() => {
		const id = vendor.id;
		loadingHistory = true;
		getScreeningHistory(id)
			.then((rows) => {
				history = rows;
			})
			.catch(() => {
				toast('Failed to load screening history', 'error');
			})
			.finally(() => {
				loadingHistory = false;
			});
	});

	function fmt(iso: string | null): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric',
			hour: 'numeric',
			minute: '2-digit'
		});
	}

	function errMsg(err: unknown, fallback: string): string {
		const e = err as { detail?: string; message?: string } | null;
		return e?.detail ?? e?.message ?? fallback;
	}

	async function reScreen() {
		busy = 'screen';
		try {
			const updated = await screenVendor(vendor.id);
			onupdated(updated);
			history = await getScreeningHistory(vendor.id);
			toast('Vendor re-screened', 'success');
		} catch (err) {
			toast(errMsg(err, 'Screening failed'), 'error');
		} finally {
			busy = '';
		}
	}

	async function recompute() {
		busy = 'risk';
		try {
			const risk = await recomputeVendorRisk(vendor.id);
			onupdated({ ...vendor, risk_score: risk.risk_score, risk_level: risk.risk_level });
			toast('Risk recomputed', 'success');
		} catch (err) {
			toast(errMsg(err, 'Recompute failed'), 'error');
		} finally {
			busy = '';
		}
	}

	async function toggleBlock() {
		busy = 'block';
		try {
			const updated = vendor.payments_blocked
				? await unblockVendor(vendor.id)
				: await blockVendor(vendor.id, 'Blocked from vendor screening review');
			onupdated(updated);
			toast(updated.payments_blocked ? 'Payments blocked' : 'Payments unblocked', 'success');
		} catch (err) {
			toast(errMsg(err, 'Update failed'), 'error');
		} finally {
			busy = '';
		}
	}
</script>

<Modal open ariaLabel="Vendor screening and risk" width="lg" {onclose}>
	<h2>{vendor.name}</h2>

	<section class="panel">
		<h3>Screening &amp; Risk</h3>

		<div class="kv-grid">
			<div class="kv">
				<span class="kv-label">Screening status</span>
				<span class="kv-value">
					<ScreeningBadge screening={vendor.screening_status} blocked={vendor.payments_blocked} />
				</span>
			</div>
			<div class="kv">
				<span class="kv-label">Last screened</span>
				<span class="kv-value">{fmt(vendor.last_screened_at)}</span>
			</div>
			<div class="kv">
				<span class="kv-label">Risk level</span>
				<span class="kv-value">
					{RISK_LEVEL_LABELS[vendor.risk_level]}{#if vendor.risk_score}
						<span class="muted"> · {vendor.risk_score}</span>
					{/if}
				</span>
			</div>
			<div class="kv">
				<span class="kv-label">Payments</span>
				<span class="kv-value">
					{#if vendor.payments_blocked}
						<span class="blocked-text">Blocked</span>
						{#if vendor.payments_blocked_reason}
							<span class="muted"> — {vendor.payments_blocked_reason}</span>
						{/if}
					{:else}
						Allowed
					{/if}
				</span>
			</div>
		</div>

		<div class="actions-row">
			{#if canMutate}
				<RowAction onclick={reScreen} disabled={busy !== ''}>
					{busy === 'screen' ? 'Screening…' : 'Re-screen now'}
				</RowAction>
				<RowAction onclick={recompute} disabled={busy !== ''}>
					{busy === 'risk' ? 'Recomputing…' : 'Recompute risk'}
				</RowAction>
			{/if}
			{#if canBlock}
				<RowAction
					variant="danger"
					onclick={toggleBlock}
					disabled={busy !== ''}
				>
					{#if busy === 'block'}
						Working…
					{:else}
						{vendor.payments_blocked ? 'Unblock payments' : 'Block payments'}
					{/if}
				</RowAction>
			{/if}
			{#if !canMutate && !canBlock}
				<span class="muted">Re-screen, recompute, and block actions need the right permission.</span>
			{/if}
		</div>
	</section>

	<section class="panel">
		<h3>Screening history</h3>
		{#if loadingHistory}
			<p class="muted">Loading…</p>
		{:else if history.length === 0}
			<p class="muted">No screening checks yet.</p>
		{:else}
			<ul class="timeline">
				{#each history as check (check.id)}
					{@const screened = asScreeningStatus(check.result)}
					<li class="timeline-item">
						<span class="timeline-when">{fmt(check.checked_at)}</span>
						<span class="timeline-body">
							{#if screened}
								<ScreeningBadge screening={screened} />
							{:else}
								<span class="result-text">{check.result}</span>
							{/if}
							<span class="muted">{check.provider} · {check.check_type}</span>
							{#if check.matched_list}
								<span class="matched-list">matched: {check.matched_list}</span>
							{/if}
							{#if check.risk_score}
								<span class="muted">score {check.risk_score}</span>
							{/if}
						</span>
					</li>
				{/each}
			</ul>
		{/if}
	</section>

	<div class="modal-footer">
		<button type="button" class="btn-cancel" onclick={onclose}>Close</button>
	</div>
</Modal>

<style>
	.panel {
		margin-top: 16px;
	}
	.panel h3 {
		font-size: 0.85rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		margin: 0 0 10px;
	}
	.kv-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 12px 24px;
	}
	.kv {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.kv-label {
		font-size: 0.72rem;
		color: var(--text-muted);
	}
	.kv-value {
		font-size: 0.9rem;
	}
	.muted {
		color: var(--text-muted);
	}
	.blocked-text {
		color: #e04040;
		font-weight: 500;
	}
	.actions-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin-top: 14px;
	}
	.timeline {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.timeline-item {
		display: grid;
		grid-template-columns: 150px 1fr;
		gap: 12px;
		align-items: baseline;
		font-size: 0.82rem;
		padding-bottom: 8px;
		border-bottom: 1px solid var(--border);
	}
	.timeline-item:last-child {
		border-bottom: none;
	}
	.timeline-when {
		color: var(--text-muted);
		white-space: nowrap;
	}
	.timeline-body {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
	}
	.result-text {
		font-weight: 500;
	}
	.matched-list {
		color: #e04040;
		font-weight: 500;
	}
</style>
