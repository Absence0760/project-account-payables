<script lang="ts">
	import { auth } from '$lib/stores/auth.svelte';
	import { getAuditExport, downloadAuditExportCsv } from '$lib/api/audit';
	import {
		verifyInvoiceSignatures,
		verifySignaturesForPeriod,
		type InvoiceSignatureReport,
		type SignatureVerificationReport
	} from '$lib/api/auditVerification';
	import type { AuditEntry, AuditFieldChange } from '$lib/types/audit';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { formatDate } from '$lib/utils/time';
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

	// ---------------------------------------------------------------------
	// Approval-signature verification (the SOX non-repudiation control test).
	//
	// Two scopes over one panel, mirroring the export controls above: a period
	// population sweep (`GET /api/audit/verify-signatures`) and the per-invoice
	// drill-down (`GET /api/audit/invoice/{id}/verify-signatures`). Both are
	// admin/CFO-gated and BOTH WRITE AN `audit.viewed` ACCESS ROW, so they run
	// only on an explicit click — never on mount, never polled. A speculative
	// fetch would put an access event nobody asked for into the auditor's own
	// evidence trail.
	// ---------------------------------------------------------------------

	// The findings list the backend caps at; the population counts it returns
	// are never truncated, which is why the truncation notice says so.
	const VERIFY_FINDINGS_LIMIT = 100;

	let verifyStart = $state(daysAgo(90));
	let verifyEnd = $state(today);
	let verifyLoading = $state(false);
	let verifyError = $state('');
	let report = $state<SignatureVerificationReport | null>(null);

	// The drill-down: one finding's invoice, re-checked approval by approval.
	// The population sweep names the rows to investigate; this is what an
	// auditor opens next. Loaded only when a specific finding is clicked.
	let drillInvoice = $state<{ id: string; label: string } | null>(null);
	let drillLoading = $state(false);
	let drillError = $state('');
	let invoiceReport = $state<InvoiceSignatureReport | null>(null);

	// Narrowed out of the possibly-null state so the DataTable `body()` snippet
	// (a closure, which loses `{#if report}` narrowing) can read them.
	const findings = $derived(report?.findings ?? []);
	const invoiceApprovals = $derived(invoiceReport?.approvals ?? []);

	let VERIFY_COLUMNS = $derived([
		{ label: m('audit.verify.col.invoice') },
		{ label: m('audit.verify.col.actor') },
		{ label: m('audit.verify.col.signedAt') },
		{ label: m('audit.verify.col.auditRow') },
		{ label: m('audit.verify.col.verdict') }
	]);

	let INVOICE_VERIFY_COLUMNS = $derived([
		{ label: m('audit.verify.col.signedAt') },
		{ label: m('audit.verify.col.actor') },
		{ label: m('audit.verify.col.auditRow') },
		{ label: m('audit.verify.col.verdict') }
	]);

	/** A row's `signed_at` is echoed back verbatim — including the unparseable
	 *  string that made it a finding — so render it as a date only when it
	 *  actually parses, and never swallow the claim itself. */
	function signedAtLabel(value: string | null): string {
		if (!value) return '—';
		const parsed = formatDate(value, '', { hour: 'numeric', minute: '2-digit' });
		return parsed || value;
	}

	async function runVerification() {
		verifyError = '';
		verifyLoading = true;
		// A fresh population run invalidates whatever drill-down was open.
		drillInvoice = null;
		invoiceReport = null;
		drillError = '';
		try {
			report = await verifySignaturesForPeriod({
				start: verifyStart,
				end: verifyEnd,
				limit: VERIFY_FINDINGS_LIMIT
			});
		} catch (e) {
			verifyError = e instanceof Error ? e.message : m('audit.verify.error');
			report = null;
		} finally {
			verifyLoading = false;
		}
	}

	async function drillIntoInvoice(invoiceId: string, label: string) {
		drillInvoice = { id: invoiceId, label };
		drillError = '';
		drillLoading = true;
		invoiceReport = null;
		try {
			invoiceReport = await verifyInvoiceSignatures(invoiceId);
		} catch (e) {
			drillError = e instanceof Error ? e.message : m('audit.verify.error');
		} finally {
			drillLoading = false;
		}
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

	<!--
		Approval-signature verification — the SOX non-repudiation control test.
		`invalid` and `unsigned` are rendered as two separate counts, with their
		own wording and their own colour, because they are different claims:
		`invalid` means the digest no longer re-derives (investigate), while
		`unsigned` means there is no signature to check at all — what a
		signing-key rollout backlog looks like. Merging them into one "problems"
		figure would read a key rollout as fraud.
	-->
	<section class="verify-panel" aria-labelledby="verify-heading">
		<h2 id="verify-heading">{m('audit.verify.title')}</h2>
		<p class="verify-intro">{m('audit.verify.intro')}</p>

		<div class="audit-controls">
			<label>
				{m('audit.field.from')}
				<input type="date" bind:value={verifyStart} max={verifyEnd} />
			</label>
			<label>
				{m('audit.field.to')}
				<input type="date" bind:value={verifyEnd} min={verifyStart} max={today} />
			</label>
			<button
				class="btn-primary"
				data-testid="run-verification"
				onclick={runVerification}
				disabled={verifyLoading}
			>
				{verifyLoading ? m('audit.verify.running') : m('audit.verify.run')}
			</button>
		</div>

		{#if verifyError}
			<p class="audit-error" role="alert" data-testid="verify-error">{verifyError}</p>
		{/if}

		{#if report}
			<div class="kpi-row" data-testid="verify-counts">
				<KpiCard value={String(report.approvals_checked)} label={m('audit.verify.kpi.checked')} />
				<KpiCard value={String(report.invoices_covered)} label={m('audit.verify.kpi.invoices')} />
				<KpiCard
					value={String(report.valid)}
					label={m('audit.verify.kpi.valid')}
					highlight={report.valid > 0 ? 'green' : null}
				/>
				<!-- Red only for `invalid`: a tampered row is the alarm. -->
				<KpiCard
					value={String(report.invalid)}
					label={m('audit.verify.kpi.invalid')}
					highlight={report.invalid > 0 ? 'red' : null}
					sub={m('audit.verify.sub.invalid')}
				/>
				<!-- Deliberately never red, and never folded into `invalid`. -->
				<KpiCard
					value={String(report.unsigned)}
					label={m('audit.verify.kpi.unsigned')}
					sub={m('audit.verify.sub.unsigned')}
				/>
			</div>

			{#if !report.signing_configured}
				<p class="verify-notice" data-testid="verify-not-configured">
					{m('audit.verify.notConfigured')}
				</p>
			{/if}

			<p class="verify-legend">{m('audit.verify.legend')}</p>

			{#if report.approvals_checked === 0}
				<p class="verify-state" data-testid="verify-none">{m('audit.verify.noApprovals')}</p>
			{:else if report.invalid === 0 && report.unsigned === 0}
				<p class="verify-clean" data-testid="verify-clean">{m('audit.verify.clean')}</p>
			{:else}
				<h3 class="verify-findings-heading">{m('audit.verify.findings')}</h3>
				{#if report.findings_truncated}
					<p class="verify-state">
						{m('audit.verify.truncated', { limit: String(VERIFY_FINDINGS_LIMIT) })}
					</p>
				{/if}
				<DataTable columns={VERIFY_COLUMNS} isEmpty={findings.length === 0} empty={m('audit.verify.noApprovals')}>
					{#snippet body()}
						{#each findings as f (f.audit_row_id)}
							<tr data-testid="verify-finding" data-verdict={f.verdict}>
								<td>
									<RowLink
										ariaLabel={m('audit.verify.drillTitle', {
											invoice: f.invoice_number ?? f.invoice_id
										})}
										onclick={() => drillIntoInvoice(f.invoice_id, f.invoice_number ?? f.invoice_id)}
									>
										{f.invoice_number ?? f.invoice_id.slice(0, 8)}
									</RowLink>
								</td>
								<td>{f.actor ?? '—'}</td>
								<td class="mono">{signedAtLabel(f.signed_at)}</td>
								<td class="mono">{f.audit_row_id.slice(0, 8)}</td>
								<td>
									{#if f.verdict === 'invalid'}
										<Badge tone="danger" variant="verdict-invalid">
											{m('audit.verify.verdict.invalid')}
										</Badge>
									{:else}
										<Badge tone="muted" variant="verdict-unsigned">
											{m('audit.verify.verdict.unsigned')}
										</Badge>
									{/if}
								</td>
							</tr>
						{/each}
					{/snippet}
				</DataTable>
			{/if}
		{/if}

		{#if drillInvoice}
			<div class="verify-drill" data-testid="verify-drill">
				<h3>{m('audit.verify.drillTitle', { invoice: drillInvoice.label })}</h3>
				{#if drillLoading}
					<p class="verify-state">{m('audit.verify.running')}</p>
				{:else if drillError}
					<p class="audit-error" role="alert">{drillError}</p>
				{:else if invoiceReport}
					{#if !invoiceReport.signing_configured}
						<p class="verify-notice">{m('audit.verify.notConfigured')}</p>
					{/if}
					<DataTable
						columns={INVOICE_VERIFY_COLUMNS}
						isEmpty={invoiceApprovals.length === 0}
						empty={m('audit.verify.noApprovals')}
					>
						{#snippet body()}
							{#each invoiceApprovals as a (a.audit_row_id)}
								<tr data-testid="verify-approval">
									<td class="mono">{signedAtLabel(a.signed_at)}</td>
									<td>{a.actor ?? '—'}</td>
									<td class="mono">{a.audit_row_id.slice(0, 8)}</td>
									<td>
										{#if !a.signed}
											<Badge tone="muted" variant="verdict-unsigned">
												{m('audit.verify.verdict.unsigned')}
											</Badge>
										{:else if a.valid}
											<Badge tone="success" variant="verdict-valid">
												{m('audit.verify.verdict.valid')}
											</Badge>
										{:else}
											<Badge tone="danger" variant="verdict-invalid">
												{m('audit.verify.verdict.invalid')}
											</Badge>
										{/if}
									</td>
								</tr>
							{/each}
						{/snippet}
					</DataTable>
				{/if}
			</div>
		{/if}
	</section>
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
		color: var(--danger);
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

	/* --- Approval-signature verification panel --- */
	.verify-panel {
		margin-top: 32px;
		padding-top: 24px;
		border-top: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.verify-panel h2 {
		margin: 0;
		font-size: 1.1rem;
	}

	.verify-panel h3 {
		margin: 0;
		font-size: 0.95rem;
	}

	.verify-intro,
	.verify-legend {
		margin: 0;
		color: var(--text-muted);
		font-size: 0.85rem;
		max-width: 900px;
	}

	/* The "signing is off" explanation. Warning-toned, never danger-toned:
	   an unconfigured key is a deployment fact, not a forensic finding. */
	.verify-notice {
		margin: 0;
		padding: 10px 12px;
		border-radius: 6px;
		background: var(--warning-tint);
		color: var(--warning-on-tint);
		font-size: 0.85rem;
		max-width: 900px;
	}

	.verify-clean {
		margin: 0;
		padding: 10px 12px;
		border-radius: 6px;
		background: var(--success-tint);
		color: var(--success-on-tint);
		font-size: 0.9rem;
		max-width: 900px;
	}

	.verify-state {
		margin: 0;
		color: var(--text-muted);
		font-size: 0.85rem;
	}

	.verify-drill {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
</style>
