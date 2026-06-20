<script lang="ts">
	import { goto } from '$app/navigation';
	import { auth } from '$lib/stores/auth.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import {
		listApiKeys,
		createApiKey,
		revokeApiKey,
		getApiKeyUsage
	} from '$lib/api/apiKeys';
	import type { ApiKey, ApiKeyCreated, ApiKeyUsage } from '$lib/types/apiKeys';

	// RBAC: the backend gates every /api/api-keys endpoint to admin only and 403s
	// the rest. Wait for `auth.user` to resolve before redirecting so we don't
	// bounce before /me lands (the billing/discounts/audit pages document the
	// same race). `isAdmin` is exactly the admin role.
	const userLoaded = $derived(auth.user !== null);
	const allowed = $derived(auth.isAdmin);

	$effect(() => {
		if (userLoaded && !allowed) goto('/');
	});

	const COLUMNS = [
		{ label: 'Name' },
		{ label: 'Key prefix' },
		{ label: 'Scopes' },
		{ label: 'Created' },
		{ label: 'Last used' },
		{ label: 'Status' },
		{ class: 'actions-col' }
	];

	let keys = $state<ApiKey[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Create flow.
	let creating = $state(false);
	let newName = $state('');
	let saving = $state(false);

	// One-time plaintext reveal (after a successful mint).
	let minted = $state<ApiKeyCreated | null>(null);
	let copied = $state(false);

	// Revoke confirm (armed two-click on the row action).
	let confirmRevokeId = $state<string | null>(null);

	// Per-key usage view.
	let usageKey = $state<ApiKey | null>(null);
	let usage = $state<ApiKeyUsage | null>(null);
	let usageLoading = $state(false);
	let usageError = $state<string | null>(null);
	// Most-recent 30 days, narrowed away from null so the table snippet (a
	// closure, which loses the `{:else if usage}` narrowing) can read it.
	const usageDays = $derived(usage ? usage.daily.slice(0, 30) : []);

	async function load() {
		loading = true;
		error = null;
		try {
			keys = await listApiKeys();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load API keys.';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		// Only fetch once we know the role is allowed (avoids a guaranteed 403 for
		// a non-admin before the redirect fires).
		if (userLoaded && allowed) load();
	});

	function openCreate() {
		newName = '';
		creating = true;
	}

	async function handleCreate() {
		const name = newName.trim();
		if (!name) return;
		saving = true;
		try {
			const created = await createApiKey(name);
			creating = false;
			newName = '';
			copied = false;
			// Show the plaintext exactly once. Keep the list fresh too.
			minted = created;
			await load();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to create key', 'error');
		} finally {
			saving = false;
		}
	}

	async function copyKey() {
		if (!minted) return;
		try {
			await navigator.clipboard.writeText(minted.key);
			copied = true;
			toast('API key copied to clipboard', 'success');
		} catch {
			toast('Copy failed — select and copy the key manually', 'error');
		}
	}

	function dismissMinted() {
		// Drop the plaintext from memory the moment the reveal closes.
		minted = null;
		copied = false;
	}

	async function handleRevoke(id: string) {
		try {
			await revokeApiKey(id);
			toast('API key revoked', 'success');
			await load();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to revoke key', 'error');
		} finally {
			confirmRevokeId = null;
		}
	}

	async function openUsage(key: ApiKey) {
		usageKey = key;
		usage = null;
		usageError = null;
		usageLoading = true;
		try {
			usage = await getApiKeyUsage(key.id, 30);
		} catch (e) {
			usageError = e instanceof Error ? e.message : 'Failed to load usage.';
		} finally {
			usageLoading = false;
		}
	}

	function handleWindowClick(e: MouseEvent) {
		if (confirmRevokeId && !(e.target as HTMLElement).closest('.row-action')) {
			confirmRevokeId = null;
		}
	}

	function fmtDate(d: string | null): string {
		if (!d) return '—';
		const dt = new Date(d);
		if (Number.isNaN(dt.getTime())) return d;
		return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
	}

	function isRevoked(k: ApiKey): boolean {
		return k.revoked_at !== null;
	}
</script>

<svelte:window onclick={handleWindowClick} />

<PageHeader title="API Keys">
	{#snippet actions()}
		<button class="btn-primary" onclick={openCreate}>+ Create key</button>
	{/snippet}

	<p class="page-hint">
		Programmatic keys authenticate the public Developer API (<code>X-API-Key</code>). Each key is
		scoped to this workspace and carries read access. The full key is shown only once at
		creation — store it somewhere safe.
	</p>

	{#if loading}
		<p class="state" data-testid="api-keys-loading">Loading API keys…</p>
	{:else if error}
		<div class="state error" data-testid="api-keys-error" role="alert">
			<p>{error}</p>
			<button type="button" class="btn-cancel" onclick={load}>Retry</button>
		</div>
	{:else}
		<DataTable
			columns={COLUMNS}
			isEmpty={keys.length === 0}
			empty="No API keys yet. Create one to use the Developer API."
		>
			{#snippet body()}
				{#each keys as key (key.id)}
					<tr
						class="clickable"
						class:revoked={isRevoked(key)}
						onclick={(e) => {
							if (isRowOpenClick(e)) openUsage(key);
						}}
					>
						<td>
							<RowLink onclick={() => openUsage(key)} ariaLabel={`View usage for ${key.name}`}>
								{key.name}
							</RowLink>
						</td>
						<td class="mono">{key.key_prefix}…</td>
						<td>{key.scopes.join(', ')}</td>
						<td>{fmtDate(key.created_at)}</td>
						<td>{fmtDate(key.last_used_at)}</td>
						<td>
							{#if isRevoked(key)}
								<span class="status-pill revoked">Revoked</span>
							{:else}
								<span class="status-pill active">Active</span>
							{/if}
						</td>
						<td class="actions">
							{#if !isRevoked(key)}
								<RowAction
									variant="danger"
									armed={confirmRevokeId === key.id}
									onclick={(e) => {
										e.stopPropagation();
										if (confirmRevokeId === key.id) {
											handleRevoke(key.id);
										} else {
											confirmRevokeId = key.id;
										}
									}}
								>
									{confirmRevokeId === key.id ? 'Confirm' : 'Revoke'}
								</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>
	{/if}
</PageHeader>

<!-- Create key modal -->
<Modal open={creating} ariaLabel="Create API key" width="sm" onclose={() => (creating = false)}>
	<h2>Create API key</h2>
	<p class="modal-hint">
		Give the key a descriptive name (e.g. the integration or script that will use it). It will
		be minted with <strong>read</strong> scope.
	</p>
	<form
		onsubmit={(e) => {
			e.preventDefault();
			handleCreate();
		}}
	>
		<label>
			<span>Name <em class="required">*</em></span>
			<input type="text" bind:value={newName} required maxlength="120" placeholder="e.g. Reporting sync" />
		</label>
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (creating = false)}>Cancel</button>
			<button type="submit" class="btn-primary" disabled={!newName.trim() || saving}>
				{saving ? 'Creating…' : 'Create'}
			</button>
		</div>
	</form>
</Modal>

<!-- One-time plaintext reveal -->
<Modal
	open={minted !== null}
	ariaLabel="API key created"
	width="md"
	onclose={dismissMinted}
>
	{#if minted}
		<h2>API key created</h2>
		<div class="reveal-warning" role="alert">
			<strong>Copy this key now.</strong> For security it is shown only once and can never be
			retrieved again. If you lose it, revoke it and create a new one.
		</div>
		<div class="key-reveal">
			<code class="key-value" data-testid="minted-key">{minted.key}</code>
			<button type="button" class="btn-primary copy-btn" onclick={copyKey}>
				{copied ? 'Copied' : 'Copy'}
			</button>
		</div>
		<dl class="reveal-meta">
			<div>
				<dt>Name</dt>
				<dd>{minted.api_key.name}</dd>
			</div>
			<div>
				<dt>Prefix</dt>
				<dd class="mono">{minted.api_key.key_prefix}…</dd>
			</div>
		</dl>
		<div class="modal-footer">
			<button type="button" class="btn-primary" onclick={dismissMinted}>Done</button>
		</div>
	{/if}
</Modal>

<!-- Per-key usage view -->
<Modal open={usageKey !== null} ariaLabel="API key usage" width="md" onclose={() => (usageKey = null)}>
	{#if usageKey}
		<h2>Usage — {usageKey.name}</h2>
		{#if usageLoading}
			<p class="state" data-testid="usage-loading">Loading usage…</p>
		{:else if usageError}
			<div class="state error" role="alert">
				<p>{usageError}</p>
				<button type="button" class="btn-cancel" onclick={() => openUsage(usageKey!)}>Retry</button>
			</div>
		{:else if usage}
			<div class="usage-totals" data-testid="usage-totals">
				<div class="usage-stat">
					<span class="usage-num">{usage.total_requests.toLocaleString()}</span>
					<span class="usage-lbl">Total requests</span>
				</div>
				<div class="usage-stat">
					<span class="usage-num">{usage.window_requests.toLocaleString()}</span>
					<span class="usage-lbl">Last {usage.window_days} days</span>
				</div>
				<div class="usage-stat">
					<span class="usage-num">{fmtDate(usage.last_used_at)}</span>
					<span class="usage-lbl">Last used</span>
				</div>
			</div>

			<h3 class="usage-heading">Recent activity</h3>
			{#if usage.daily.length === 0}
				<p class="state">No requests recorded yet.</p>
			{:else}
				<DataTable
					columns={[{ label: 'Date' }, { label: 'Requests', class: 'num-col' }]}
					isEmpty={usageDays.length === 0}
					empty="No requests recorded yet."
				>
					{#snippet body()}
						{#each usageDays as day (day.usage_date)}
							<tr>
								<td>{fmtDate(day.usage_date)}</td>
								<td class="num-col">{day.request_count.toLocaleString()}</td>
							</tr>
						{/each}
					{/snippet}
				</DataTable>
			{/if}
		{/if}
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (usageKey = null)}>Close</button>
		</div>
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

	.status-pill {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.status-pill.active {
		background: rgba(50, 200, 130, 0.15);
		color: #26b977;
	}

	.status-pill.revoked {
		background: rgba(138, 143, 160, 0.15);
		color: var(--text-muted);
	}

	tr.revoked td:not(.actions) {
		opacity: 0.6;
	}

	/* One-time reveal */
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

	/* Usage view */
	.usage-totals {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
		gap: 1rem;
		margin-bottom: 1.25rem;
	}

	.usage-stat {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}

	.usage-num {
		font-size: 1.25rem;
		font-weight: 700;
	}

	.usage-lbl {
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.usage-heading {
		margin: 0 0 0.5rem;
		font-size: 0.95rem;
	}

	.num-col {
		text-align: right;
	}
</style>
