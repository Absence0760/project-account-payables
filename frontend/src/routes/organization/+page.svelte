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

	interface ErpConfig {
		type: string;
		integration_method: string;
		api_key: string;
		account_token: string;
		// Direct adapter fields
		base_url: string;
		tenant_id: string;
		client_id: string;
		client_secret: string;
		environment: string;
		company_id: string;
		account_id: string;
		consumer_key: string;
		consumer_secret: string;
		token_id: string;
		token_secret: string;
	}

	interface OrgSettings {
		company: CompanyProfile;
		invoice_defaults: InvoiceDefaults;
		erp?: ErpConfig;
	}

	const ERP_TYPES = [
		{ value: 'dynamics_365_bc', label: 'Microsoft Dynamics 365 Business Central' },
		{ value: 'sap_s4hana', label: 'SAP S/4HANA' },
		{ value: 'netsuite', label: 'Oracle NetSuite' },
		{ value: 'epicor', label: 'Epicor Kinetic' },
		{ value: 'acumatica', label: 'Acumatica Cloud ERP' },
		{ value: 'sage_x3', label: 'Sage X3' },
		{ value: 'infor', label: 'Infor CloudSuite Industrial' },
		{ value: 'qad', label: 'QAD Adaptive' },
		{ value: 'cetec', label: 'Cetec ERP' },
		{ value: 'delmiaworks', label: 'DELMIAWorks' },
	];

	interface OrgResponse {
		id: string;
		name: string;
		slug: string;
		plan: string;
		settings: OrgSettings;
		created_at: string;
	}

	let org = $state<OrgResponse | null>(null);
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
	// ERP
	let erpType = $state('dynamics_365_bc');
	let erpMethod = $state('merge_dev');
	let erpApiKey = $state('');
	let erpAccountToken = $state('');
	let erpBaseUrl = $state('');
	let erpTenantId = $state('');
	let erpClientId = $state('');
	let erpClientSecret = $state('');
	let erpEnvironment = $state('production');
	let erpCompanyId = $state('');
	let erpAccountId = $state('');
	let erpConsumerKey = $state('');
	let erpConsumerSecret = $state('');
	let erpTokenId = $state('');
	let erpTokenSecret = $state('');
	let testingConnection = $state(false);
	// Extraction
	let extractionProgramType = $state('platform');
	let extractionProvider = $state('claude_vision');
	let extractionApiKey = $state('');
	let extractionAwsKeyId = $state('');
	let extractionAwsSecret = $state('');
	let extractionAwsRegion = $state('us-east-1');
	let savingExtraction = $state(false);

	const EXTRACTION_PROVIDERS = [
		{ value: 'claude_vision', label: 'Claude Vision (Anthropic)' },
		{ value: 'openai_vision', label: 'GPT-4V (OpenAI)' },
		{ value: 'aws_textract', label: 'AWS Textract' },
	];
	// Cards
	let cardsEnabled = $state(false);
	let cardsProgramType = $state('platform');
	let cardsProvider = $state('');
	let cardsRegion = $state('US');
	let cardsApiKey = $state('');
	let cardsClientId = $state('');
	let cardsClientSecret = $state('');
	let cardsCustomerHashId = $state('');
	let cardsWalletHashId = $state('');
	let cardsExpiryDays = $state(30);
	let cardsSandbox = $state(true);
	let savingCards = $state(false);

	const CARD_REGIONS = [
		{ value: 'US', label: 'United States', default_provider: 'lithic' },
		{ value: 'UK', label: 'United Kingdom', default_provider: 'lithic' },
		{ value: 'DE', label: 'Germany (EU)', default_provider: 'lithic' },
		{ value: 'FR', label: 'France (EU)', default_provider: 'lithic' },
		{ value: 'NL', label: 'Netherlands (EU)', default_provider: 'lithic' },
		{ value: 'ZA', label: 'South Africa', default_provider: 'nium' },
		{ value: 'AU', label: 'Australia', default_provider: 'nium' },
		{ value: 'SG', label: 'Singapore', default_provider: 'nium' },
		{ value: 'HK', label: 'Hong Kong', default_provider: 'nium' },
		{ value: 'IN', label: 'India', default_provider: 'nium' },
		{ value: 'CA', label: 'Canada', default_provider: 'nium' },
		{ value: 'AE', label: 'UAE', default_provider: 'nium' },
		{ value: 'JP', label: 'Japan', default_provider: 'nium' },
	];

	let autoProvider = $derived(
		CARD_REGIONS.find(r => r.value === cardsRegion)?.default_provider ?? 'nium'
	);
	let effectiveProvider = $derived(cardsProvider || autoProvider);
	let connectionResult = $state<{ success: boolean; message: string } | null>(null);

	async function testConnection() {
		testingConnection = true;
		connectionResult = null;
		try {
			connectionResult = await api.post<{ success: boolean; message: string }>('/api/organization/test-erp', {
				type: erpType,
				integration_method: erpMethod,
				api_key: erpApiKey,
				account_token: erpAccountToken,
				base_url: erpBaseUrl,
				tenant_id: erpTenantId,
				client_id: erpClientId,
				client_secret: erpClientSecret,
				environment: erpEnvironment,
				company_id: erpCompanyId,
				account_id: erpAccountId,
				consumer_key: erpConsumerKey,
				consumer_secret: erpConsumerSecret,
				token_id: erpTokenId,
				token_secret: erpTokenSecret,
			});
		} catch (err) {
			connectionResult = { success: false, message: err instanceof Error ? err.message : 'Test failed' };
		} finally {
			testingConnection = false;
		}
	}

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
			// ERP
			const erp = (data.settings as unknown as Record<string, unknown>).erp as ErpConfig | undefined;
			if (erp) {
				erpType = erp.type || 'dynamics_365_bc';
				erpMethod = erp.integration_method || 'merge_dev';
				erpApiKey = erp.api_key || '';
				erpAccountToken = erp.account_token || '';
				erpBaseUrl = erp.base_url || '';
				erpTenantId = erp.tenant_id || '';
				erpClientId = erp.client_id || '';
				erpClientSecret = erp.client_secret || '';
				erpEnvironment = erp.environment || 'production';
				erpCompanyId = erp.company_id || '';
				erpAccountId = erp.account_id || '';
				erpConsumerKey = erp.consumer_key || '';
				erpConsumerSecret = erp.consumer_secret || '';
				erpTokenId = erp.token_id || '';
				erpTokenSecret = erp.token_secret || '';
			}
			// Cards
			const cards = (data.settings as unknown as Record<string, unknown>).cards as Record<string, unknown> | undefined;
			if (cards) {
				cardsEnabled = (cards.enabled as boolean) ?? false;
				cardsProgramType = (cards.program_type as string) || 'platform';
				cardsProvider = (cards.provider as string) || '';
				cardsRegion = (cards.region as string) || 'US';
				cardsApiKey = (cards.api_key as string) || '';
				cardsClientId = (cards.client_id as string) || '';
				cardsClientSecret = (cards.client_secret as string) || '';
				cardsCustomerHashId = (cards.customer_hash_id as string) || '';
				cardsWalletHashId = (cards.wallet_hash_id as string) || '';
				cardsExpiryDays = (cards.default_expiry_days as number) || 30;
				cardsSandbox = (cards.sandbox as boolean) ?? true;
			}
			// Extraction
			const extraction = (data.settings as unknown as Record<string, unknown>).extraction as Record<string, unknown> | undefined;
			if (extraction) {
				extractionProgramType = (extraction.program_type as string) || 'platform';
				extractionProvider = (extraction.provider as string) || 'claude_vision';
				extractionApiKey = (extraction.api_key as string) || '';
				extractionAwsKeyId = (extraction.aws_access_key_id as string) || '';
				extractionAwsSecret = (extraction.aws_secret_access_key as string) || '';
				extractionAwsRegion = (extraction.aws_region as string) || 'us-east-1';
			}
		} catch {
			toast('Failed to load organization', 'error');
		}
	}

	let savingProfile = $state(false);
	let savingDefaults = $state(false);
	let savingErp = $state(false);

	async function patchSettings(section: string, partial: Record<string, unknown>) {
		const current = org ? (org.settings as unknown as Record<string, unknown>) : {};
		const merged = { ...current, ...partial };
		const data = await api.patch<OrgResponse>('/api/organization', {
			...(partial.company ? { name: name.trim() } : {}),
			settings: merged,
		});
		org = data;
		toast(`${section} saved`, 'success');
	}

	async function saveProfile() {
		savingProfile = true;
		try {
			await patchSettings('Company profile', {
				company: {
					address, phone, website,
					tax_id: taxId,
					logo_url: org?.settings.company.logo_url ?? '',
				},
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Save failed', 'error');
		} finally {
			savingProfile = false;
		}
	}

	async function saveDefaults() {
		savingDefaults = true;
		try {
			await patchSettings('Invoice defaults', {
				invoice_defaults: {
					currency,
					payment_terms: paymentTerms,
					number_prefix: numberPrefix,
					default_gl_account: defaultGl,
					default_cost_center: defaultCostCenter,
				},
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Save failed', 'error');
		} finally {
			savingDefaults = false;
		}
	}

	async function saveErp() {
		savingErp = true;
		try {
			await patchSettings('ERP integration', {
				erp: {
					type: erpType,
					integration_method: erpMethod,
					api_key: erpApiKey,
					account_token: erpAccountToken,
					base_url: erpBaseUrl,
					tenant_id: erpTenantId,
					client_id: erpClientId,
					client_secret: erpClientSecret,
					environment: erpEnvironment,
					company_id: erpCompanyId,
					account_id: erpAccountId,
					consumer_key: erpConsumerKey,
					consumer_secret: erpConsumerSecret,
					token_id: erpTokenId,
					token_secret: erpTokenSecret,
				},
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Save failed', 'error');
		} finally {
			savingErp = false;
		}
	}

	async function saveExtraction() {
		savingExtraction = true;
		try {
			await patchSettings('AI Extraction', {
				extraction: {
					program_type: extractionProgramType,
					provider: extractionProvider,
					api_key: extractionApiKey,
					aws_access_key_id: extractionAwsKeyId,
					aws_secret_access_key: extractionAwsSecret,
					aws_region: extractionAwsRegion,
				},
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Save failed', 'error');
		} finally {
			savingExtraction = false;
		}
	}

	async function saveCards() {
		savingCards = true;
		try {
			await patchSettings('Virtual cards', {
				cards: {
					enabled: cardsEnabled,
					program_type: cardsProgramType,
					provider: cardsProvider || autoProvider,
					region: cardsRegion,
					api_key: cardsApiKey,
					client_id: cardsClientId,
					client_secret: cardsClientSecret,
					customer_hash_id: cardsCustomerHashId,
					wallet_hash_id: cardsWalletHashId,
					default_expiry_days: cardsExpiryDays,
					sandbox: cardsSandbox,
				},
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Save failed', 'error');
		} finally {
			savingCards = false;
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
				<div class="section-footer">
					<button class="btn-save-section" disabled={savingProfile} onclick={saveProfile}>
						{savingProfile ? 'Saving...' : 'Save Profile'}
					</button>
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
				<div class="section-footer">
					<button class="btn-save-section" disabled={savingDefaults} onclick={saveDefaults}>
						{savingDefaults ? 'Saving...' : 'Save Defaults'}
					</button>
				</div>
			</section>

			<section class="card">
				<h2>AI Extraction</h2>
				<p class="card-hint">Configure how invoice data is extracted from uploaded files. Platform mode uses our AI — charged per extraction. BYOK uses your own API key.</p>

				<div class="form-grid">
					<label>
						<span>Program</span>
						<select bind:value={extractionProgramType}>
							<option value="platform">Platform (per-invoice fee)</option>
							<option value="byok">Bring Your Own Key (free)</option>
						</select>
					</label>
					{#if extractionProgramType === 'byok'}
						<label>
							<span>Provider</span>
							<select bind:value={extractionProvider}>
								{#each EXTRACTION_PROVIDERS as p}
									<option value={p.value}>{p.label}</option>
								{/each}
							</select>
						</label>
					{:else}
						<label>
							<span>Provider</span>
							<input type="text" value="Claude Vision (Anthropic)" disabled />
						</label>
					{/if}
				</div>

				{#if extractionProgramType === 'platform'}
					<p class="card-hint" style="margin-top: 10px;">Extractions use our Claude Vision API. No API key needed. Usage is tracked and billed per extraction.</p>
				{:else if extractionProvider === 'claude_vision' || extractionProvider === 'openai_vision'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>{extractionProvider === 'claude_vision' ? 'Anthropic' : 'OpenAI'} API Key</span>
							<input type="password" bind:value={extractionApiKey} placeholder="sk-..." />
						</label>
					</div>
				{:else if extractionProvider === 'aws_textract'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>AWS Access Key ID</span>
							<input type="text" bind:value={extractionAwsKeyId} />
						</label>
						<label>
							<span>AWS Secret Access Key</span>
							<input type="password" bind:value={extractionAwsSecret} />
						</label>
						<label>
							<span>AWS Region</span>
							<input type="text" bind:value={extractionAwsRegion} placeholder="us-east-1" />
						</label>
					</div>
				{/if}

				<div class="section-footer">
					<button class="btn-save-section" disabled={savingExtraction} onclick={saveExtraction}>
						{savingExtraction ? 'Saving...' : 'Save Extraction Settings'}
					</button>
				</div>
			</section>

			<section class="card">
				<h2>ERP Integration</h2>
				<p class="card-hint">Connect to your ERP system for invoice posting and payment tracking.</p>
				<div class="form-grid">
					<label>
						<span>ERP System</span>
						<select bind:value={erpType}>
							{#each ERP_TYPES as erp}
								<option value={erp.value}>{erp.label}</option>
							{/each}
						</select>
					</label>
					<label>
						<span>Integration Method</span>
						<select bind:value={erpMethod}>
							<option value="merge_dev">Merge.dev (Unified API)</option>
							<option value="direct">Direct API Connection</option>
						</select>
					</label>
				</div>

				{#if erpMethod === 'merge_dev'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>Merge.dev API Key</span>
							<input type="password" bind:value={erpApiKey} placeholder="test_..." />
						</label>
						<label>
							<span>Account Token</span>
							<input type="password" bind:value={erpAccountToken} placeholder="Customer linked account token" />
						</label>
					</div>
					<p class="card-hint" style="margin-top: 8px;">Get your API key from the <a href="https://app.merge.dev" target="_blank" rel="noopener">Merge.dev dashboard</a>. Account tokens are created when customers connect their ERP via Merge Link.</p>
				{:else if erpType === 'dynamics_365_bc'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>Base URL</span>
							<input type="url" bind:value={erpBaseUrl} placeholder="https://api.businesscentral.dynamics.com/v2.0" />
						</label>
						<label>
							<span>Environment</span>
							<input type="text" bind:value={erpEnvironment} placeholder="production" />
						</label>
						<label>
							<span>Azure Tenant ID</span>
							<input type="text" bind:value={erpTenantId} />
						</label>
						<label>
							<span>Client ID</span>
							<input type="text" bind:value={erpClientId} />
						</label>
						<label>
							<span>Client Secret</span>
							<input type="password" bind:value={erpClientSecret} />
						</label>
						<label>
							<span>Company ID</span>
							<input type="text" bind:value={erpCompanyId} />
						</label>
					</div>
				{:else if erpType === 'netsuite'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>Account ID</span>
							<input type="text" bind:value={erpAccountId} placeholder="1234567" />
						</label>
						<label>
							<span>Consumer Key</span>
							<input type="text" bind:value={erpConsumerKey} />
						</label>
						<label>
							<span>Consumer Secret</span>
							<input type="password" bind:value={erpConsumerSecret} />
						</label>
						<label>
							<span>Token ID</span>
							<input type="text" bind:value={erpTokenId} />
						</label>
						<label>
							<span>Token Secret</span>
							<input type="password" bind:value={erpTokenSecret} />
						</label>
					</div>
				{:else}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>API Base URL</span>
							<input type="url" bind:value={erpBaseUrl} />
						</label>
						<label>
							<span>API Key / Client ID</span>
							<input type="password" bind:value={erpClientId} />
						</label>
						<label>
							<span>API Secret / Client Secret</span>
							<input type="password" bind:value={erpClientSecret} />
						</label>
					</div>
					<p class="card-hint" style="margin-top: 8px;">Direct integration for {ERP_TYPES.find(e => e.value === erpType)?.label} is coming soon. Use Merge.dev in the meantime.</p>
				{/if}

				<div class="erp-test-row">
					<button class="btn-save-section" disabled={savingErp} onclick={saveErp}>
						{savingErp ? 'Saving...' : 'Save ERP Settings'}
					</button>
					<button class="btn-test" disabled={testingConnection} onclick={testConnection}>
						{testingConnection ? 'Testing...' : 'Test Connection'}
					</button>
					{#if connectionResult}
						<span class="test-result" class:success={connectionResult.success} class:failure={!connectionResult.success}>
							{connectionResult.message}
						</span>
					{/if}
				</div>
			</section>

			<section class="card">
				<h2>Virtual Cards</h2>
				<p class="card-hint">Issue single-use virtual cards for invoice payments. Earn rebates on every transaction.</p>

				<div class="form-grid">
					<label>
						<span>Enabled</span>
						<select bind:value={cardsEnabled}>
							<option value={false}>Disabled</option>
							<option value={true}>Enabled</option>
						</select>
					</label>
					<label>
						<span>Card Program</span>
						<select bind:value={cardsProgramType}>
							<option value="platform">Platform (recommended)</option>
							<option value="byok">Bring Your Own Keys</option>
						</select>
					</label>
					<label>
						<span>Region</span>
						<select bind:value={cardsRegion}>
							{#each CARD_REGIONS as r}
								<option value={r.value}>{r.label}</option>
							{/each}
						</select>
					</label>
					<label>
						<span>Card Expiry (days)</span>
						<input type="number" min="1" max="90" bind:value={cardsExpiryDays} />
					</label>
				</div>

				{#if cardsEnabled && cardsProgramType === 'platform'}
					<p class="card-hint" style="margin-top: 10px;">Cards are issued through our platform. Provider is auto-selected based on your region ({autoProvider === 'lithic' ? 'Lithic' : 'Nium'}). No API keys needed.</p>
				{/if}

				{#if cardsEnabled && cardsProgramType === 'byok'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>Provider</span>
							<select bind:value={cardsProvider}>
								<option value="">Auto ({autoProvider === 'lithic' ? 'Lithic' : 'Nium'})</option>
								<option value="lithic">Lithic (US/UK/EU)</option>
								<option value="nium">Nium (Global)</option>
							</select>
						</label>
					</div>

					{#if effectiveProvider === 'lithic'}
						<div class="form-grid" style="margin-top: 14px;">
							<label>
								<span>Lithic API Key</span>
								<input type="password" bind:value={cardsApiKey} placeholder="api-key-..." />
							</label>
							<label>
								<span>Sandbox Mode</span>
								<select bind:value={cardsSandbox}>
									<option value={true}>Sandbox (testing)</option>
									<option value={false}>Production</option>
								</select>
							</label>
						</div>
					{:else if effectiveProvider === 'nium'}
						<div class="form-grid" style="margin-top: 14px;">
							<label>
								<span>Client ID</span>
								<input type="text" bind:value={cardsClientId} />
							</label>
							<label>
								<span>Client Secret</span>
								<input type="password" bind:value={cardsClientSecret} />
							</label>
							<label>
								<span>Customer Hash ID</span>
								<input type="text" bind:value={cardsCustomerHashId} />
							</label>
							<label>
								<span>Wallet Hash ID</span>
								<input type="text" bind:value={cardsWalletHashId} />
							</label>
							<label>
								<span>Sandbox Mode</span>
								<select bind:value={cardsSandbox}>
									<option value={true}>Sandbox (testing)</option>
									<option value={false}>Production</option>
								</select>
							</label>
						</div>
					{/if}
				{/if}

				<div class="section-footer">
					<button class="btn-save-section" disabled={savingCards} onclick={saveCards}>
						{savingCards ? 'Saving...' : 'Save Card Settings'}
					</button>
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

	.section-footer {
		display: flex;
		justify-content: flex-start;
		margin-top: 16px;
		padding-top: 14px;
		border-top: 1px solid var(--border);
	}

	.btn-save-section {
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

	.btn-save-section:hover:not(:disabled) {
		opacity: 0.85;
	}

	.btn-save-section:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.erp-test-row {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-top: 16px;
		padding-top: 14px;
		border-top: 1px solid var(--border);
	}

	.btn-test {
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

	.btn-test:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}

	.btn-test:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.test-result {
		font-size: 0.85rem;
		font-weight: 500;
	}

	.test-result.success {
		color: #1fa86a;
	}

	.test-result.failure {
		color: #e04040;
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
