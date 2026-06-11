<script lang="ts">
	import { auth } from '$lib/stores/auth.svelte';
	import { getAuditExport, downloadAuditExportCsv } from '$lib/api/audit';
	import type { AuditEntry, AuditFieldChange } from '$lib/types/audit';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';

	// RBAC parity with the backend: the auditor export is admin/CFO only (the
	// backend 403s everyone else). Gate the page content on the loaded user's
	// role rather than redirecting — a redirect races the async /me load (the
	// user's roles aren't known on first paint). `auth.user` is non-null only
	// after /me resolves, so we wait for it before deciding access.
	const userLoaded = $derived(auth.user !== null);
	const allowed = $derived(auth.isCfo);

	const COLUMNS = [
		{ label: 'When' },
		{ label: 'Action' },
		{ label: 'Entity' },
		{ label: 'Actor' },
		{ label: 'Changes' }
	];

	const today = new Date().toISOString().slice(0, 10);
	function daysAgo(n: number): string {
		const d = new Date();
		d.setDate(d.getDate() - n);
		return d.toISOString().slice(0, 10);
	}

	let mode = $state<'range' | 'invoice'>('range');
	let start = $state(daysAgo(30));
	let end = $state(today);
	let invoiceId = $state('');
	let entityType = $state('');
	let entries = $state<AuditEntry[]>([]);
	let loading = $state(false);
	let ran = $state(false);
	let error = $state('');

	function changesOf(entry: AuditEntry): [string, AuditFieldChange][] {
		const changes = entry.details?.changes;
		if (!changes || typeof changes !== 'object') return [];
		return Object.entries(changes as Record<string, AuditFieldChange>);
	}

	function currentParams() {
		return mode === 'invoice'
			? { invoiceId: invoiceId.trim() }
			: { start, end, entityType: entityType.trim() || undefined };
	}

	async function runQuery() {
		if (mode === 'invoice' && !invoiceId.trim()) {
			error = 'Enter an invoice ID.';
			return;
		}
		error = '';
		loading = true;
		ran = true;
		try {
			entries = await getAuditExport(currentParams());
		} catch (e) {
			error = e instanceof Error ? e.message : 'Query failed.';
			entries = [];
		} finally {
			loading = false;
		}
	}

	async function downloadCsv() {
		try {
			const blob = await downloadAuditExportCsv(currentParams());
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `audit_export_${today}.csv`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Download failed.', 'error');
		}
	}

	function fmt(iso: string): string {
		if (!iso) return '';
		const d = new Date(iso);
		return d.toLocaleString();
	}
</script>

<PageHeader title="Audit Trail">
	{#snippet actions()}
		{#if userLoaded && allowed}
			<button class="btn-primary" onclick={downloadCsv} disabled={!ran || entries.length === 0}>
				Download CSV
			</button>
		{/if}
	{/snippet}

	{#if userLoaded && !allowed}
		<p class="audit-denied" role="alert">
			You do not have permission to view the audit trail. This console is
			limited to admins and CFOs.
		</p>
	{:else if !userLoaded}
		<p class="audit-loading">Loading…</p>
	{:else}
	<div class="audit-controls">
		<div class="mode-toggle" role="tablist" aria-label="Audit query mode">
			<button
				role="tab"
				aria-selected={mode === 'range'}
				class:active={mode === 'range'}
				onclick={() => (mode = 'range')}>Date range</button
			>
			<button
				role="tab"
				aria-selected={mode === 'invoice'}
				class:active={mode === 'invoice'}
				onclick={() => (mode = 'invoice')}>By invoice</button
			>
		</div>

		{#if mode === 'range'}
			<label>
				From
				<input type="date" bind:value={start} max={end} />
			</label>
			<label>
				To
				<input type="date" bind:value={end} min={start} max={today} />
			</label>
			<label>
				Entity
				<input
					type="text"
					bind:value={entityType}
					placeholder="all (e.g. invoice, payment, vendor)"
				/>
			</label>
		{:else}
			<label class="invoice-input">
				Invoice ID
				<input type="text" bind:value={invoiceId} placeholder="UUID" />
			</label>
		{/if}

		<button class="btn-primary" onclick={runQuery} disabled={loading}>
			{loading ? 'Loading…' : 'Run query'}
		</button>
	</div>

	{#if error}
		<p class="audit-error" role="alert">{error}</p>
	{/if}

	<DataTable
		columns={COLUMNS}
		isEmpty={ran && !loading && entries.length === 0}
		empty="No audit entries for this query."
	>
		{#snippet body()}
			{#each entries as entry (entry.id)}
				<tr>
					<td class="mono">{fmt(entry.created_at)}</td>
					<td>{entry.action}</td>
					<td>{entry.entity_type ?? ''}{entry.entity_id ? ` · ${entry.entity_id.slice(0, 8)}` : ''}</td>
					<td>{entry.actor_name ?? entry.actor_email ?? '—'}</td>
					<td>
						{#if changesOf(entry).length > 0}
							<ul class="cell-changes">
								{#each changesOf(entry) as [field, change] (field)}
									<li>
										<span class="cf">{field}:</span>
										<span class="co">{change.old ?? '—'}</span>
										→
										<span class="cn">{change.new ?? '—'}</span>
									</li>
								{/each}
							</ul>
						{:else if entry.details?.fields}
							<span class="muted">viewed: {(entry.details.fields as string[]).join(', ')}</span>
						{:else}
							<span class="muted">—</span>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>
	{/if}
</PageHeader>

<style>
	.audit-controls {
		display: flex;
		align-items: flex-end;
		gap: 16px;
		flex-wrap: wrap;
		margin-bottom: 8px;
	}

	.audit-controls label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.85em;
		color: var(--text-muted);
	}

	.audit-controls input {
		padding: 7px 10px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--surface);
		color: var(--text);
		font-size: 0.95rem;
	}

	.invoice-input input {
		min-width: 320px;
	}

	.mode-toggle {
		display: inline-flex;
		border: 1px solid var(--border);
		border-radius: 6px;
		overflow: hidden;
	}

	.mode-toggle button {
		padding: 7px 14px;
		background: var(--surface);
		color: var(--text-muted);
		border: none;
		cursor: pointer;
		font-size: 0.9rem;
	}

	.mode-toggle button.active {
		background: var(--accent);
		color: #fff;
	}

	.audit-error {
		color: #e04040;
		margin: 0 0 8px;
	}

	.audit-denied {
		color: var(--text-muted);
		padding: 24px;
		text-align: center;
	}

	.audit-loading {
		color: var(--text-muted);
		padding: 24px;
		text-align: center;
	}

	.cell-changes {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
		font-size: 0.85em;
	}

	.cf {
		font-weight: 600;
		color: var(--text-muted);
	}

	.co {
		text-decoration: line-through;
		color: var(--text-muted);
	}

	.cn {
		color: var(--text);
		font-weight: 500;
	}

	.muted {
		color: var(--text-muted);
	}
</style>
