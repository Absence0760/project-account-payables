<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { auth } from '$lib/stores/auth.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import {
		listWebhookSubscriptions,
		createWebhookSubscription,
		updateWebhookSubscription,
		deleteWebhookSubscription,
		listWebhookDeliveries,
		redeliverWebhookDelivery
	} from '$lib/api/webhooks';
	import {
		WEBHOOK_EVENT_TYPES,
		type WebhookSubscription,
		type WebhookSubscriptionCreated,
		type WebhookDelivery
	} from '$lib/types/webhooks';

	// RBAC: the backend gates every /api/webhooks endpoint to admin only and
	// 403s the rest. Wait for `auth.user` to resolve before redirecting so we
	// don't bounce before /me lands (api-keys / billing document the same race).
	const userLoaded = $derived(auth.user !== null);
	const allowed = $derived(auth.isAdmin);

	$effect(() => {
		if (userLoaded && !allowed) goto('/');
	});

	// ── Subscriptions ────────────────────────────────────────────────────────
	const SUB_COLUMNS = [
		{ label: 'Name' },
		{ label: 'Target URL' },
		{ label: 'Events' },
		{ label: 'Secret' },
		{ label: 'Created' },
		{ label: 'Status' },
		{ class: 'actions-col' }
	];

	let subs = $state<WebhookSubscription[]>([]);
	let subsLoading = $state(true);
	let subsError = $state<string | null>(null);

	// Create flow.
	let creating = $state(false);
	let newName = $state('');
	let newUrl = $state('');
	let newEvents = $state<Set<string>>(new Set(['invoice.approved']));
	let saving = $state(false);

	// One-time secret reveal (after a successful create).
	let minted = $state<WebhookSubscriptionCreated | null>(null);
	let copied = $state(false);

	// Edit flow.
	let editing = $state<WebhookSubscription | null>(null);
	let editName = $state('');
	let editUrl = $state('');
	let editEvents = $state<Set<string>>(new Set());
	let editActive = $state(true);
	let editSaving = $state(false);

	// Delete confirm (armed two-click on the row action).
	let confirmDeleteId = $state<string | null>(null);

	async function loadSubs() {
		subsLoading = true;
		subsError = null;
		try {
			subs = await listWebhookSubscriptions();
		} catch (e) {
			subsError = e instanceof Error ? e.message : 'Failed to load webhooks.';
		} finally {
			subsLoading = false;
		}
	}

	function openCreate() {
		newName = '';
		newUrl = '';
		newEvents = new Set(['invoice.approved']);
		creating = true;
	}

	function toggleNewEvent(evt: string) {
		const next = new Set(newEvents);
		if (next.has(evt)) next.delete(evt);
		else next.add(evt);
		newEvents = next;
	}

	async function handleCreate() {
		const name = newName.trim();
		const url = newUrl.trim();
		if (!name || !url || newEvents.size === 0) return;
		saving = true;
		try {
			const created = await createWebhookSubscription({
				name,
				target_url: url,
				event_types: [...newEvents]
			});
			creating = false;
			copied = false;
			// Show the secret exactly once. Keep the list fresh too.
			minted = created;
			await loadSubs();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to create webhook', 'error');
		} finally {
			saving = false;
		}
	}

	async function copySecret() {
		if (!minted) return;
		try {
			await navigator.clipboard.writeText(minted.signing_secret);
			copied = true;
			toast('Signing secret copied to clipboard', 'success');
		} catch {
			toast('Copy failed — select and copy the secret manually', 'error');
		}
	}

	function dismissMinted() {
		// Drop the secret from memory the moment the reveal closes.
		minted = null;
		copied = false;
	}

	function openEdit(sub: WebhookSubscription) {
		editing = sub;
		editName = sub.name;
		editUrl = sub.target_url;
		editEvents = new Set(sub.event_types);
		editActive = sub.active;
	}

	function toggleEditEvent(evt: string) {
		const next = new Set(editEvents);
		if (next.has(evt)) next.delete(evt);
		else next.add(evt);
		editEvents = next;
	}

	async function handleEdit() {
		if (!editing) return;
		const name = editName.trim();
		const url = editUrl.trim();
		if (!name || !url || editEvents.size === 0) return;
		editSaving = true;
		try {
			await updateWebhookSubscription(editing.id, {
				name,
				target_url: url,
				event_types: [...editEvents],
				active: editActive
			});
			editing = null;
			toast('Webhook updated', 'success');
			await loadSubs();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to update webhook', 'error');
		} finally {
			editSaving = false;
		}
	}

	async function handleDelete(id: string) {
		try {
			await deleteWebhookSubscription(id);
			toast('Webhook deleted', 'success');
			await loadSubs();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to delete webhook', 'error');
		} finally {
			confirmDeleteId = null;
		}
	}

	// ── Deliveries ───────────────────────────────────────────────────────────
	const DELIVERY_COLUMNS = [
		{ label: 'Event' },
		{ label: 'Event ID' },
		{ label: 'Attempts' },
		{ label: 'Response' },
		{ label: 'Last attempt' },
		{ label: 'Status' },
		{ class: 'actions-col' }
	];

	const DELIVERY_STATUSES = ['pending', 'delivered', 'failed', 'dead'] as const;

	let deliveries = $state<WebhookDelivery[]>([]);
	let deliveriesLoading = $state(true);
	let deliveriesError = $state<string | null>(null);
	let redeliveringId = $state<string | null>(null);

	// URL-backed status filter (so a deep link / reload preserves the view).
	const statusFilter = $derived($page.url.searchParams.get('status') ?? 'all');

	const deliveryChips = $derived([
		{ key: 'all', label: 'All' },
		...DELIVERY_STATUSES.map((s) => ({ key: s, label: s.charAt(0).toUpperCase() + s.slice(1) }))
	]);

	function setStatusFilter(next: string) {
		const url = new URL($page.url);
		if (next === 'all') url.searchParams.delete('status');
		else url.searchParams.set('status', next);
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	async function loadDeliveries() {
		deliveriesLoading = true;
		deliveriesError = null;
		try {
			deliveries = await listWebhookDeliveries({
				status: statusFilter === 'all' ? undefined : statusFilter,
				pageSize: 50
			});
		} catch (e) {
			deliveriesError = e instanceof Error ? e.message : 'Failed to load deliveries.';
		} finally {
			deliveriesLoading = false;
		}
	}

	async function handleRedeliver(d: WebhookDelivery) {
		redeliveringId = d.id;
		try {
			await redeliverWebhookDelivery(d.id);
			toast('Delivery re-queued', 'success');
			await loadDeliveries();
		} catch (e) {
			// 409 when the delivery is already delivered — surface the backend
			// message rather than crashing.
			toast(e instanceof Error ? e.message : 'Failed to redeliver', 'error');
		} finally {
			redeliveringId = null;
		}
	}

	function canRedeliver(d: WebhookDelivery): boolean {
		return d.status === 'failed' || d.status === 'dead';
	}

	// ── Lifecycle ────────────────────────────────────────────────────────────
	$effect(() => {
		// Only fetch once we know the role is allowed (avoids a guaranteed 403
		// for a non-admin before the redirect fires).
		if (userLoaded && allowed) loadSubs();
	});

	$effect(() => {
		// Re-fetch deliveries whenever the role resolves or the status filter
		// (URL-backed) changes. Reading `statusFilter` registers the dependency.
		const s = statusFilter;
		void s;
		if (userLoaded && allowed) loadDeliveries();
	});

	function handleWindowClick(e: MouseEvent) {
		if (confirmDeleteId && !(e.target as HTMLElement).closest('.row-action')) {
			confirmDeleteId = null;
		}
	}

	function fmtDate(d: string | null): string {
		if (!d) return '—';
		const dt = new Date(d);
		if (Number.isNaN(dt.getTime())) return d;
		return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
	}
</script>

<svelte:window onclick={handleWindowClick} />

<PageHeader title="Webhooks">
	{#snippet actions()}
		<button class="btn-primary" onclick={openCreate}>+ Create webhook</button>
	{/snippet}

	<p class="page-hint">
		Outbound webhooks push platform events (invoice approved, payment settled, exception raised) to
		your endpoint as signed JSON. Each subscription's HMAC signing secret is shown only once at
		creation — store it somewhere safe and use it to verify the
		<code>X-Webhook-Signature</code> header.
	</p>

	<section aria-labelledby="subs-heading">
		<h2 id="subs-heading" class="section-heading">Subscriptions</h2>
		{#if subsLoading}
			<p class="state" data-testid="webhooks-loading">Loading webhooks…</p>
		{:else if subsError}
			<div class="state error" data-testid="webhooks-error" role="alert">
				<p>{subsError}</p>
				<button type="button" class="btn-cancel" onclick={loadSubs}>Retry</button>
			</div>
		{:else}
			<DataTable
				columns={SUB_COLUMNS}
				isEmpty={subs.length === 0}
				empty="No webhooks yet. Create one to receive event pushes."
			>
				{#snippet body()}
					{#each subs as sub (sub.id)}
						<tr
							class="clickable"
							class:inactive={!sub.active}
							onclick={(e) => {
								if (isRowOpenClick(e)) openEdit(sub);
							}}
						>
							<td>
								<RowLink onclick={() => openEdit(sub)} ariaLabel={`Edit ${sub.name}`}>
									{sub.name}
								</RowLink>
							</td>
							<td class="url-cell" title={sub.target_url}>{sub.target_url}</td>
							<td class="events-cell">{sub.event_types.join(', ')}</td>
							<td class="mono">{sub.secret_prefix}…</td>
							<td>{fmtDate(sub.created_at)}</td>
							<td>
								{#if sub.active}
									<span class="status-pill active">Active</span>
								{:else}
									<span class="status-pill paused">Inactive</span>
								{/if}
							</td>
							<td class="actions">
								<RowAction
									variant="danger"
									armed={confirmDeleteId === sub.id}
									onclick={(e) => {
										e.stopPropagation();
										if (confirmDeleteId === sub.id) {
											handleDelete(sub.id);
										} else {
											confirmDeleteId = sub.id;
										}
									}}
								>
									{confirmDeleteId === sub.id ? 'Confirm' : 'Delete'}
								</RowAction>
							</td>
						</tr>
					{/each}
				{/snippet}
			</DataTable>
		{/if}
	</section>

	<section aria-labelledby="deliveries-heading">
		<h2 id="deliveries-heading" class="section-heading">Deliveries</h2>

		<FilterChips
			chips={deliveryChips}
			active={statusFilter}
			onchange={setStatusFilter}
		/>

		{#if deliveriesLoading}
			<p class="state" data-testid="deliveries-loading">Loading deliveries…</p>
		{:else if deliveriesError}
			<div class="state error" data-testid="deliveries-error" role="alert">
				<p>{deliveriesError}</p>
				<button type="button" class="btn-cancel" onclick={loadDeliveries}>Retry</button>
			</div>
		{:else}
			<DataTable
				columns={DELIVERY_COLUMNS}
				isEmpty={deliveries.length === 0}
				empty="No deliveries yet."
			>
				{#snippet body()}
					{#each deliveries as d (d.id)}
						<tr>
							<td>{d.event_type}</td>
							<td class="mono">{d.event_id}</td>
							<td>{d.attempt_count}</td>
							<td>{d.response_code ?? '—'}</td>
							<td>{fmtDate(d.last_attempt_at)}</td>
							<td>
								<span class="status-pill {d.status}">{d.status}</span>
							</td>
							<td class="actions">
								{#if canRedeliver(d)}
									<RowAction
										disabled={redeliveringId === d.id}
										onclick={() => handleRedeliver(d)}
									>
										{redeliveringId === d.id ? 'Redelivering…' : 'Redeliver'}
									</RowAction>
								{/if}
							</td>
						</tr>
					{/each}
				{/snippet}
			</DataTable>
		{/if}
	</section>
</PageHeader>

<!-- Create webhook modal -->
<Modal open={creating} ariaLabel="Create webhook" width="md" onclose={() => (creating = false)}>
	<h2>Create webhook</h2>
	<p class="modal-hint">
		We POST signed JSON to the target URL each time a subscribed event fires. The signing secret is
		minted now and shown <strong>only once</strong>.
	</p>
	<form
		onsubmit={(e) => {
			e.preventDefault();
			handleCreate();
		}}
	>
		<label>
			<span>Name <em class="required">*</em></span>
			<input
				type="text"
				bind:value={newName}
				required
				maxlength="120"
				placeholder="e.g. Ops alerts"
			/>
		</label>
		<label>
			<span>Target URL <em class="required">*</em></span>
			<input
				type="url"
				bind:value={newUrl}
				required
				maxlength="2048"
				placeholder="https://example.com/webhooks/ap"
			/>
		</label>
		<fieldset class="events-field">
			<legend>Events <em class="required">*</em></legend>
			{#each WEBHOOK_EVENT_TYPES as evt (evt)}
				<label class="checkbox-line">
					<input
						type="checkbox"
						checked={newEvents.has(evt)}
						onchange={() => toggleNewEvent(evt)}
					/>
					<span class="mono">{evt}</span>
				</label>
			{/each}
		</fieldset>
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (creating = false)}>Cancel</button>
			<button
				type="submit"
				class="btn-primary"
				disabled={!newName.trim() || !newUrl.trim() || newEvents.size === 0 || saving}
			>
				{saving ? 'Creating…' : 'Create'}
			</button>
		</div>
	</form>
</Modal>

<!-- One-time signing-secret reveal -->
<Modal open={minted !== null} ariaLabel="Webhook created" width="md" onclose={dismissMinted}>
	{#if minted}
		<h2>Webhook created</h2>
		<div class="reveal-warning" role="alert">
			<strong>Copy this signing secret now.</strong> For security it is shown only once and can
			never be retrieved again. If you lose it, delete the webhook and create a new one.
		</div>
		<div class="key-reveal">
			<code class="key-value" data-testid="minted-secret">{minted.signing_secret}</code>
			<button type="button" class="btn-primary copy-btn" onclick={copySecret}>
				{copied ? 'Copied' : 'Copy'}
			</button>
		</div>
		<dl class="reveal-meta">
			<div>
				<dt>Name</dt>
				<dd>{minted.subscription.name}</dd>
			</div>
			<div>
				<dt>Prefix</dt>
				<dd class="mono">{minted.subscription.secret_prefix}…</dd>
			</div>
		</dl>
		<div class="modal-footer">
			<button type="button" class="btn-primary" onclick={dismissMinted}>Done</button>
		</div>
	{/if}
</Modal>

<!-- Edit webhook modal -->
<Modal open={editing !== null} ariaLabel="Edit webhook" width="md" onclose={() => (editing = null)}>
	{#if editing}
		<h2>Edit webhook</h2>
		<form
			onsubmit={(e) => {
				e.preventDefault();
				handleEdit();
			}}
		>
			<label>
				<span>Name <em class="required">*</em></span>
				<input type="text" bind:value={editName} required maxlength="120" />
			</label>
			<label>
				<span>Target URL <em class="required">*</em></span>
				<input type="url" bind:value={editUrl} required maxlength="2048" />
			</label>
			<fieldset class="events-field">
				<legend>Events <em class="required">*</em></legend>
				{#each WEBHOOK_EVENT_TYPES as evt (evt)}
					<label class="checkbox-line">
						<input
							type="checkbox"
							checked={editEvents.has(evt)}
							onchange={() => toggleEditEvent(evt)}
						/>
						<span class="mono">{evt}</span>
					</label>
				{/each}
			</fieldset>
			<label class="checkbox-line standalone">
				<input type="checkbox" bind:checked={editActive} />
				<span>Active</span>
			</label>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (editing = null)}>Cancel</button>
				<button
					type="submit"
					class="btn-primary"
					disabled={!editName.trim() || !editUrl.trim() || editEvents.size === 0 || editSaving}
				>
					{editSaving ? 'Saving…' : 'Save'}
				</button>
			</div>
		</form>
	{/if}
</Modal>

<style>
	.page-hint {
		margin: 0;
		color: var(--text-muted);
		font-size: 0.85rem;
		max-width: 720px;
	}

	.page-hint code {
		background: var(--surface-2, #232b44);
		padding: 1px 5px;
		border-radius: 4px;
		font-size: 0.8em;
	}

	.section-heading {
		margin: 0.5rem 0 0.25rem;
		font-size: 1.05rem;
	}

	.state {
		color: var(--text-muted);
		padding: 0.75rem 0;
	}

	.state.error {
		color: #f06464;
	}

	.mono {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.85rem;
	}

	.url-cell,
	.events-cell {
		max-width: 320px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.status-pill {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.status-pill.active,
	.status-pill.delivered {
		background: rgba(50, 200, 130, 0.15);
		color: #26b977;
	}

	.status-pill.paused,
	.status-pill.pending {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}

	.status-pill.failed,
	.status-pill.dead {
		background: rgba(240, 70, 70, 0.15);
		color: #f06464;
	}

	tr.inactive td:not(.actions) {
		opacity: 0.6;
	}

	/* One-time reveal — mirrors the api-keys mint modal. */
	.reveal-warning {
		background: rgba(255, 180, 50, 0.12);
		border: 1px solid rgba(255, 180, 50, 0.35);
		color: #d4940a;
		border-radius: 8px;
		padding: 0.75rem 1rem;
		font-size: 0.85rem;
		margin-bottom: 1rem;
	}

	.key-reveal {
		display: flex;
		align-items: stretch;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}

	.key-value {
		flex: 1;
		background: var(--surface-2, #232b44);
		border: 1px solid var(--border, #2a3350);
		border-radius: 8px;
		padding: 0.6rem 0.75rem;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.85rem;
		word-break: break-all;
		user-select: all;
	}

	.copy-btn {
		white-space: nowrap;
	}

	.reveal-meta {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 0.75rem;
		margin: 0 0 0.5rem;
	}

	.reveal-meta dt {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.reveal-meta dd {
		margin: 0.15rem 0 0;
		font-weight: 600;
	}

	.events-field {
		border: 1px solid var(--border, #2a3350);
		border-radius: 8px;
		padding: 0.5rem 0.75rem 0.6rem;
		margin: 0.5rem 0;
	}

	.events-field legend {
		font-size: 0.85rem;
		padding: 0 0.35rem;
	}

	.checkbox-line {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.2rem 0;
		font-size: 0.9rem;
	}

	.checkbox-line.standalone {
		margin-top: 0.5rem;
	}
</style>
