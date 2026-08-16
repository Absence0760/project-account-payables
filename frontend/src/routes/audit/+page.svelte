<script lang="ts">
	import { auth } from '$lib/stores/auth.svelte';
	import { getAuditExport, downloadAuditExportCsv } from '$lib/api/audit';
	import type { AuditEntry, AuditFieldChange } from '$lib/types/audit';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';

	// RBAC parity with the backend: the auditor export is admin/CFO only (the
	// backend 403s everyone else). Gate the page content on the loaded user's
	// role rather than redirecting — a redirect races the async /me load (the
	// user's roles aren't known on first paint). `auth.user` is non-null only
	// after /me resolves, so we wait for it before deciding access.
	const userLoaded = $derived(auth.user !== null);
	const allowed = $derived(auth.isCfo);

	// $derived so the column headers re-render when the locale changes.
	let COLUMNS = $derived([
		{ label: m('audit.col.when') },
		{ label: m('audit.col.action') },
		{ label: m('audit.col.entity') },
		{ label: m('audit.col.actor') },
		{ label: m('audit.col.changes') }
	]);

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
			error = m('audit.error.enterInvoiceId');
			return;
		}
		error = '';
		loading = true;
		ran = true;
		try {
			entries = await getAuditExport(currentParams());
		} catch (e) {
			error = e instanceof Error ? e.message : m('audit.error.queryFailed');
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
			toast(e instanceof Error ? e.message : m('audit.error.downloadFailed'), 'error');
		}
	}

	function fmt(iso: string): string {
		if (!iso) return '';
		const d = new Date(iso);
		return d.toLocaleString();
	}
</script>

<PageHeader title={m('audit.title')}>
	{#snippet actions()}
		{#if userLoaded && allowed}
			<button class="btn-primary" onclick={downloadCsv} disabled={!ran || entries.length === 0}>
				{m('audit.downloadCsv')}
			</button>
		{/if}
	{/snippet}

	{#if userLoaded && !allowed}
		<p class="audit-denied" role="alert">
			{m('audit.denied')}
		</p>
	{:else if !userLoaded}
		<p class="audit-loading">{m('common.loading')}</p>
	{:else}
	<div class="audit-controls">
		<div class="mode-toggle" role="tablist" aria-label={m('audit.queryMode')}>
			<button
				role="tab"
				aria-selected={mode === 'range'}
				class:active={mode === 'range'}
				onclick={() => (mode = 'range')}>{m('audit.mode.dateRange')}</button
			>
			<button
				role="tab"
				aria-selected={mode === 'invoice'}
				class:active={mode === 'invoice'}
				onclick={() => (mode = 'invoice')}>{m('audit.mode.byInvoice')}</button
			>
		</div>

		{#if mode === 'range'}
			<label>
				{m('audit.field.from')}
				<input type="date" bind:value={start} max={end} />
			</label>
			<label>
				{m('audit.field.to')}
				<input type="date" bind:value={end} min={start} max={today} />
			</label>
			<label>
				{m('audit.field.entity')}
				<input
					type="text"
					bind:value={entityType}
					placeholder={m('audit.field.entityPlaceholder')}
				/>
			</label>
		{:else}
			<label class="invoice-input">
				{m('audit.field.invoiceId')}
				<input type="text" bind:value={invoiceId} placeholder={m('audit.field.invoiceIdPlaceholder')} />
			</label>
		{/if}

		<button class="btn-primary" onclick={runQuery} disabled={loading}>
			{loading ? m('common.loading') : m('audit.runQuery')}
		</button>
	</div>

	{#if error}
		<p class="audit-error" role="alert">{error}</p>
	{/if}

	<DataTable
		columns={COLUMNS}
		isEmpty={ran && !loading && entries.length === 0}
		empty={m('audit.empty')}
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
							<span class="muted">{m('audit.viewed', { fields: (entry.details.fields as string[]).join(', ') })}</span>
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
		background: var(--accent-strong);
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
