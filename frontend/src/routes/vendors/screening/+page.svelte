<script lang="ts">
	// Sanctions-screening review queue — the vendors flagged `match` / `review`
	// by the screening engine, awaiting a human decision. A separate sub-route
	// from the vendors LIST page (/vendors) so the two don't collide.
	//
	// Reviewer actions (block / unblock a vendor's payments) are gated on the
	// GRANULAR permission `vendor.block` via the auth store — NOT a role check —
	// so the control is hidden when the user lacks it (the backend enforces it
	// regardless). Re-screen is admin/ap_manager (auth.isManager). PII-safe: the
	// endpoints return names, categories/list-names, scores + statuses only.
	import {
		getScreeningReviewQueue,
		getScreeningHistory,
		blockVendor,
		unblockVendor,
		screenVendor
	} from '$lib/api/vendors';
	import type { ScreeningReviewItem, SanctionsCheck } from '$lib/types/vendor';
	import { SCREENING_STATUS_LABELS, RISK_LEVEL_LABELS } from '$lib/types/vendor';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import ScreeningBadge from '$lib/components/ui/ScreeningBadge.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import { formatDate } from '$lib/utils/time';
	import { auth } from '$lib/stores/auth.svelte';
	import { PERM_VENDOR_BLOCK } from '$lib/types/admin';

	// Reviewer capabilities — mirror the backend gates so the UI can't drift.
	// Block/unblock is the splittable granular permission; re-screen stays on
	// the admin/ap_manager role (backend `require_roles`).
	const canBlock = $derived(auth.can(PERM_VENDOR_BLOCK));
	const canRescreen = $derived(auth.isManager);

	let items = $state<ScreeningReviewItem[]>([]);
	let loading = $state(true);
	let loadError = $state(false);
	let search = $state('');

	// Detail modal — the selected vendor + its screening history.
	let selected = $state<ScreeningReviewItem | null>(null);
	let history = $state<SanctionsCheck[]>([]);
	let historyLoading = $state(false);
	let busy = $state<'block' | 'rescreen' | null>(null);
	let blockReason = $state('');

	const COLUMNS = [
		{ label: 'Vendor' },
		{ label: 'Screening' },
		{ label: 'Matched list' },
		{ label: 'Provider' },
		{ label: 'Risk score' },
		{ label: 'Last screened' },
		{ class: 'actions-col' }
	];

	$effect(() => {
		loadQueue();
	});

	// Client-side search over the loaded queue (vendor name + matched list).
	// The queue is a bounded attention list, so it's small enough to filter in
	// memory; the fetch pulls the whole set.
	let filtered = $derived(
		search.trim()
			? items.filter((it) => {
					const q = search.trim().toLowerCase();
					return (
						it.vendor_name.toLowerCase().includes(q) ||
						(it.latest_matched_list ?? '').toLowerCase().includes(q)
					);
				})
			: items
	);

	let matchCount = $derived(items.filter((it) => it.screening_status === 'match').length);
	let reviewCount = $derived(items.filter((it) => it.screening_status === 'review').length);
	let blockedCount = $derived(items.filter((it) => it.payments_blocked).length);

	// Sequences `loadQueue`. The mount `$effect` is not its only trigger — the
	// header Refresh button fires it too, so two can be in flight at once and
	// resolve out of order. `applyVendorUpdate` rewrites a row in place with no
	// fetch of its own, so it retires whatever is in flight first: otherwise a
	// queue response issued before a block/unblock/re-screen lands afterwards
	// and puts the lifted payment block — or the stale screening verdict — back
	// on the row. See `frontend/CLAUDE.md` § Sequencing list fetches.
	const fetchSequence = createRequestSequencer();

	async function loadQueue() {
		const token = fetchSequence.start();
		loading = true;
		loadError = false;
		try {
			const queue = await getScreeningReviewQueue();
			// Superseded by a newer refresh, or by a local block/re-screen.
			if (!fetchSequence.canCommit(token)) return;
			items = queue;
		} catch {
			// `isCurrentRequest`, not `canCommit`: a load superseded by a local
			// edit still failed, and no newer load is coming to report it.
			if (!fetchSequence.isCurrentRequest(token)) return;
			loadError = true;
			toast('Failed to load the screening review queue', 'error');
		} finally {
			if (fetchSequence.isCurrentRequest(token)) loading = false;
		}
	}

	async function openDetail(item: ScreeningReviewItem) {
		selected = item;
		blockReason = '';
		history = [];
		historyLoading = true;
		try {
			history = await getScreeningHistory(item.vendor_id);
		} catch {
			toast('Failed to load screening history', 'error');
		} finally {
			historyLoading = false;
		}
	}

	function closeDetail() {
		selected = null;
		history = [];
		blockReason = '';
	}

	// Reflect a mutated vendor's block/screening state back into the row + the
	// open modal. The block/unblock/screen endpoints return the full Vendor.
	function applyVendorUpdate(vendor: {
		id: string;
		screening_status: ScreeningReviewItem['screening_status'];
		payments_blocked: boolean;
		last_screened_at: string | null;
		risk_level: ScreeningReviewItem['risk_level'];
		risk_score: string | null;
	}) {
		fetchSequence.supersedeInFlight();
		items = items.map((it) =>
			it.vendor_id === vendor.id
				? {
						...it,
						screening_status: vendor.screening_status,
						payments_blocked: vendor.payments_blocked,
						last_screened_at: vendor.last_screened_at,
						risk_level: vendor.risk_level,
						risk_score: vendor.risk_score
					}
				: it
		);
		if (selected && selected.vendor_id === vendor.id) {
			selected = {
				...selected,
				screening_status: vendor.screening_status,
				payments_blocked: vendor.payments_blocked,
				last_screened_at: vendor.last_screened_at,
				risk_level: vendor.risk_level,
				risk_score: vendor.risk_score
			};
		}
	}

	async function toggleBlock() {
		if (!selected) return;
		busy = 'block';
		try {
			const updated = selected.payments_blocked
				? await unblockVendor(selected.vendor_id)
				: await blockVendor(selected.vendor_id, blockReason.trim() || undefined);
			applyVendorUpdate(updated);
			toast(updated.payments_blocked ? 'Payments blocked' : 'Payments unblocked', 'success');
			blockReason = '';
		} catch (err) {
			const e = err as { detail?: string; message?: string } | null;
			toast(e?.detail ?? e?.message ?? 'Action failed', 'error');
		} finally {
			busy = null;
		}
	}

	async function rescreen() {
		if (!selected) return;
		busy = 'rescreen';
		try {
			const updated = await screenVendor(selected.vendor_id);
			applyVendorUpdate(updated);
			toast('Vendor re-screened', 'success');
			// Refresh the history so the new screen appears at the top.
			historyLoading = true;
			try {
				history = await getScreeningHistory(selected.vendor_id);
			} finally {
				historyLoading = false;
			}
		} catch (err) {
			const e = err as { detail?: string; message?: string } | null;
			toast(e?.detail ?? e?.message ?? 'Re-screen failed', 'error');
		} finally {
			busy = null;
		}
	}
</script>

<PageHeader title="Screening Review Queue">
	{#snippet actions()}
		<button class="btn-outline" onclick={loadQueue} disabled={loading}>
			{loading ? 'Refreshing…' : 'Refresh'}
		</button>
	{/snippet}

	<div class="kpi-row">
		<KpiCard value={matchCount} label="Sanctions matches" highlight={matchCount > 0 ? 'red' : null} />
		<KpiCard value={reviewCount} label="Needs review" highlight={reviewCount > 0 ? 'red' : null} />
		<KpiCard value={blockedCount} label="Payments blocked" />
	</div>

	<div class="filter-row">
		<SearchBox
			bind:value={search}
			placeholder="Search vendor or matched list…"
			ariaLabel="Search screening review queue"
		/>
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={!loading && filtered.length === 0}
		empty={loadError
			? 'Could not load the review queue.'
			: search.trim()
				? 'No vendors match your search.'
				: 'No vendors are awaiting screening review. 🎉'}
	>
		{#snippet body()}
			{#each filtered as it (it.vendor_id)}
				<tr
					class="clickable"
					onclick={(e) => {
						if (isRowOpenClick(e)) openDetail(it);
					}}
				>
					<td class="vendor-name">
						<RowLink onclick={() => openDetail(it)} ariaLabel={`Review screening for ${it.vendor_name}`}>
							{it.vendor_name}
						</RowLink>
					</td>
					<td>
						<ScreeningBadge
							screening={it.screening_status}
							risk={it.risk_level}
							blocked={it.payments_blocked}
						/>
					</td>
					<td class="muted">{it.latest_matched_list ?? '—'}</td>
					<td class="muted">{it.latest_provider ?? '—'}</td>
					<td class="mono">{it.risk_score ?? '—'}</td>
					<td class="muted">{formatDate(it.last_screened_at)}</td>
					<td class="actions">
						<RowAction onclick={() => openDetail(it)}>Review</RowAction>
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>
</PageHeader>

<Modal
	open={selected !== null}
	ariaLabel="Vendor screening review"
	width="md"
	onclose={closeDetail}
>
	{#if selected}
		<h2>{selected.vendor_name}</h2>

		<div class="badge-row">
			<ScreeningBadge
				screening={selected.screening_status}
				risk={selected.risk_level}
				blocked={selected.payments_blocked}
			/>
		</div>

		<dl class="meta">
			<div>
				<dt>Screening status</dt>
				<dd>{SCREENING_STATUS_LABELS[selected.screening_status]}</dd>
			</div>
			<div>
				<dt>Risk level</dt>
				<dd>{RISK_LEVEL_LABELS[selected.risk_level]}{selected.risk_score ? ` (${selected.risk_score})` : ''}</dd>
			</div>
			<div>
				<dt>Matched list</dt>
				<dd>{selected.latest_matched_list ?? '—'}</dd>
			</div>
			<div>
				<dt>Provider</dt>
				<dd>{selected.latest_provider ?? '—'}</dd>
			</div>
			<div>
				<dt>Last screened</dt>
				<dd>{formatDate(selected.last_screened_at)}</dd>
			</div>
			<div>
				<dt>Payments</dt>
				<dd>{selected.payments_blocked ? 'Blocked' : 'Allowed'}</dd>
			</div>
		</dl>

		{#if canBlock || canRescreen}
			<div class="review-actions">
				{#if canRescreen}
					<button class="btn-outline" onclick={rescreen} disabled={busy !== null}>
						{busy === 'rescreen' ? 'Re-screening…' : 'Re-screen now'}
					</button>
				{/if}
				{#if canBlock}
					{#if !selected.payments_blocked}
						<input
							class="reason-input"
							type="text"
							maxlength="255"
							placeholder="Reason (optional)"
							aria-label="Block reason"
							bind:value={blockReason}
						/>
					{/if}
					<button
						class="btn-block"
						class:unblock={selected.payments_blocked}
						onclick={toggleBlock}
						disabled={busy !== null}
					>
						{#if busy === 'block'}
							Working…
						{:else}
							{selected.payments_blocked ? 'Unblock payments' : 'Block payments'}
						{/if}
					</button>
				{/if}
			</div>
		{:else}
			<p class="no-perm-note">You don't have permission to block or re-screen this vendor.</p>
		{/if}

		<h3>Screening history</h3>
		{#if historyLoading}
			<p class="muted">Loading…</p>
		{:else if history.length === 0}
			<p class="muted">No screening history yet.</p>
		{:else}
			<ul class="history">
				{#each history as h (h.id)}
					<li>
						<span class="history-result {h.result}">{h.result.replace(/_/g, ' ')}</span>
						<span class="history-meta">
							{h.check_type} · {h.provider}
							{#if h.matched_list}· {h.matched_list}{/if}
							{#if h.risk_score}· score {h.risk_score}{/if}
						</span>
						<span class="history-date">{formatDate(h.checked_at, '—', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })}</span>
					</li>
				{/each}
			</ul>
		{/if}

		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={closeDetail}>Close</button>
		</div>
	{/if}
</Modal>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */
	.btn-outline {
		padding: 8px 18px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
	}
	.btn-outline:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.btn-outline:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.vendor-name {
		font-weight: 500;
	}

	/* --- Detail modal --- */
	.badge-row {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		margin: 4px 0 14px;
	}
	.meta {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 10px 24px;
		margin: 0 0 16px;
	}
	.meta div {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.meta dt {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--text-muted);
	}
	.meta dd {
		margin: 0;
		font-size: 0.9rem;
		color: var(--text);
	}

	.review-actions {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
		padding: 12px 0;
		border-top: 1px solid var(--border);
		border-bottom: 1px solid var(--border);
		margin-bottom: 14px;
	}
	.reason-input {
		flex: 1;
		min-width: 160px;
		padding: 7px 10px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.85rem;
	}
	.reason-input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}
	.btn-block {
		padding: 8px 16px;
		border-radius: 6px;
		border: 1px solid #f06464;
		background: rgba(224, 64, 64, 0.12);
		color: #f06464;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
	}
	.btn-block:hover:not(:disabled) {
		background: rgba(224, 64, 64, 0.2);
	}
	.btn-block.unblock {
		border-color: #1fa86a;
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}
	.btn-block.unblock:hover:not(:disabled) {
		background: rgba(31, 168, 106, 0.2);
	}
	.btn-block:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.no-perm-note {
		font-size: 0.82rem;
		color: var(--text-muted);
		font-style: italic;
		padding: 12px 0;
		border-top: 1px solid var(--border);
		border-bottom: 1px solid var(--border);
		margin-bottom: 14px;
	}

	/* --- History timeline --- */
	h3 {
		font-size: 0.95rem;
		margin: 0 0 8px;
	}
	.history {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
		max-height: 260px;
		overflow-y: auto;
	}
	.history li {
		display: flex;
		align-items: baseline;
		gap: 10px;
		flex-wrap: wrap;
		padding-bottom: 8px;
		border-bottom: 1px solid var(--border);
	}
	.history-result {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: capitalize;
		padding: 2px 8px;
		border-radius: 10px;
		background: var(--bg);
		color: var(--text-muted);
		white-space: nowrap;
	}
	.history-result.match {
		background: rgba(224, 64, 64, 0.12);
		color: #f06464;
	}
	.history-result.review_required {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}
	.history-result.clear {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}
	.history-meta {
		font-size: 0.8rem;
		color: var(--text-muted);
		flex: 1;
	}
	.history-date {
		font-size: 0.76rem;
		color: var(--text-muted);
		white-space: nowrap;
	}
</style>
