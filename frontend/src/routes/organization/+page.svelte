<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/components/Toast.svelte';

	interface CompanyProfile {
		address: string;
		phone: string;
		website: string;
		tax_id: string;
		logo_url: string;
	}

	interface InvoiceDefaults {
		currency: string;
		payment_terms: string;
		number_prefix: string;
		default_gl_account: string;
		default_cost_center: string;
	}

	interface OrgSettings {
		company: CompanyProfile;
		invoice_defaults: InvoiceDefaults;
	}

	interface OrgResponse {
		id: string;
		name: string;
		slug: string;
		plan: string;
		settings: OrgSettings;
		created_at: string;
	}

	let org = $state<OrgResponse | null>(null);
	let saving = $state(false);

	// Editable fields
	let name = $state('');
	let address = $state('');
	let phone = $state('');
	let website = $state('');
	let taxId = $state('');
	let currency = $state('USD');
	let paymentTerms = $state('Net 30');
	let numberPrefix = $state('INV-');
	let defaultGl = $state('');
	let defaultCostCenter = $state('');

	$effect(() => {
		loadOrg();
	});

	async function loadOrg() {
		try {
			const data = await api.get<OrgResponse>('/api/organization');
			org = data;
			name = data.name;
			address = data.settings.company.address;
			phone = data.settings.company.phone;
			website = data.settings.company.website;
			taxId = data.settings.company.tax_id;
			currency = data.settings.invoice_defaults.currency;
			paymentTerms = data.settings.invoice_defaults.payment_terms;
			numberPrefix = data.settings.invoice_defaults.number_prefix;
			defaultGl = data.settings.invoice_defaults.default_gl_account;
			defaultCostCenter = data.settings.invoice_defaults.default_cost_center;
		} catch {
			toast('Failed to load organization', 'error');
		}
	}

	async function handleSave() {
		saving = true;
		try {
			const data = await api.patch<OrgResponse>('/api/organization', {
				name: name.trim(),
				settings: {
					company: {
						address,
						phone,
						website,
						tax_id: taxId,
						logo_url: org?.settings.company.logo_url ?? '',
					},
					invoice_defaults: {
						currency,
						payment_terms: paymentTerms,
						number_prefix: numberPrefix,
						default_gl_account: defaultGl,
						default_cost_center: defaultCostCenter,
					},
				},
			});
			org = data;
			toast('Settings saved', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Save failed', 'error');
		} finally {
			saving = false;
		}
	}

	const PLAN_LABELS: Record<string, string> = {
		free: 'Free',
		pro: 'Pro',
		enterprise: 'Enterprise',
	};
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Organization</h1>
		<button class="btn-primary" disabled={saving} onclick={handleSave}>
			{saving ? 'Saving...' : 'Save Changes'}
		</button>
	</header>

	{#if org}
		<div class="sections">
			<section class="card">
				<h2>Company Profile</h2>
				<div class="form-grid">
					<label>
						<span>Company Name</span>
						<input type="text" bind:value={name} />
					</label>
					<label>
						<span>Tax ID / EIN</span>
						<input type="text" bind:value={taxId} placeholder="XX-XXXXXXX" />
					</label>
					<label class="full-width">
						<span>Address</span>
						<textarea bind:value={address} rows="2" placeholder="Street, City, State, ZIP"></textarea>
					</label>
					<label>
						<span>Phone</span>
						<input type="tel" bind:value={phone} />
					</label>
					<label>
						<span>Website</span>
						<input type="url" bind:value={website} placeholder="https://" />
					</label>
				</div>
			</section>

			<section class="card">
				<h2>Invoice Defaults</h2>
				<p class="card-hint">These defaults are applied to new invoices.</p>
				<div class="form-grid">
					<label>
						<span>Currency</span>
						<select bind:value={currency}>
							<option value="USD">USD — US Dollar</option>
							<option value="EUR">EUR — Euro</option>
							<option value="GBP">GBP — British Pound</option>
							<option value="CAD">CAD — Canadian Dollar</option>
							<option value="AUD">AUD — Australian Dollar</option>
							<option value="JPY">JPY — Japanese Yen</option>
						</select>
					</label>
					<label>
						<span>Payment Terms</span>
						<select bind:value={paymentTerms}>
							<option value="Due on Receipt">Due on Receipt</option>
							<option value="Net 10">Net 10</option>
							<option value="Net 15">Net 15</option>
							<option value="Net 30">Net 30</option>
							<option value="Net 45">Net 45</option>
							<option value="Net 60">Net 60</option>
							<option value="Net 90">Net 90</option>
							<option value="2/10 Net 30">2/10 Net 30</option>
						</select>
					</label>
					<label>
						<span>Invoice Number Prefix</span>
						<input type="text" bind:value={numberPrefix} placeholder="INV-" />
					</label>
					<label>
						<span>Default GL Account</span>
						<input type="text" bind:value={defaultGl} placeholder="e.g. 6100" />
					</label>
					<label>
						<span>Default Cost Center</span>
						<input type="text" bind:value={defaultCostCenter} placeholder="e.g. ADMIN" />
					</label>
				</div>
			</section>

			<section class="card plan-card">
				<h2>Plan</h2>
				<div class="plan-info">
					<span class="plan-badge">{PLAN_LABELS[org.plan] ?? org.plan}</span>
					<span class="plan-slug">Tenant: <code>{org.slug}</code></span>
					<span class="plan-date">Created: {new Date(org.created_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}</span>
				</div>
			</section>
		</div>
	{:else}
		<div class="loading">Loading...</div>
	{/if}
</div>

<style>
	.workspace {
		max-width: 800px;
		margin: 0 auto;
		padding: 24px 20px;
		display: flex;
		flex-direction: column;
		gap: 16px;
		min-height: 100vh;
	}

	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	h1 {
		font-size: 1.3rem;
		font-weight: 700;
		margin: 0;
	}

	.btn-primary {
		padding: 8px 18px;
		border-radius: 6px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
	}

	.btn-primary:hover:not(:disabled) {
		opacity: 0.85;
	}

	.btn-primary:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.sections {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 20px 24px;
	}

	.card h2 {
		font-size: 1rem;
		font-weight: 600;
		margin: 0 0 4px;
	}

	.card-hint {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0 0 14px;
	}

	.form-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 14px;
		margin-top: 14px;
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
	select,
	textarea {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.88rem;
		color: var(--text);
		font-family: inherit;
		width: 100%;
		box-sizing: border-box;
	}

	textarea {
		resize: vertical;
	}

	input:focus,
	select:focus,
	textarea:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.plan-card h2 {
		margin-bottom: 12px;
	}

	.plan-info {
		display: flex;
		align-items: center;
		gap: 16px;
	}

	.plan-badge {
		display: inline-block;
		padding: 4px 12px;
		border-radius: 12px;
		background: rgba(99, 140, 255, 0.12);
		color: var(--accent);
		font-size: 0.85rem;
		font-weight: 600;
	}

	.plan-slug {
		font-size: 0.82rem;
		color: var(--text-muted);
	}

	.plan-slug code {
		background: var(--bg);
		padding: 2px 6px;
		border-radius: 3px;
		font-size: 0.8rem;
	}

	.plan-date {
		font-size: 0.82rem;
		color: var(--text-muted);
	}

	.loading {
		text-align: center;
		padding: 40px;
		color: var(--text-muted);
	}

	@media (max-width: 600px) {
		.form-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
