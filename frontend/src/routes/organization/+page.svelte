<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import type { MessageKey } from '$lib/i18n/messages';
	import { formatDate } from '$lib/utils/time';
	import {
		getChatNotifications,
		revokeChatWebhook,
		rotateChatWebhook,
		updateChatNotifications
	} from '$lib/api/chatNotifications';
	import {
		CHAT_EVENT_LABELS,
		CHAT_PROVIDER_LABELS,
		type ChatNotificationStatus
	} from '$lib/types/chatNotifications';

	interface CompanyProfile {
		address: string;
		phone: string;
		website: string;
		tax_id: string;
		logo_url: string;
		vat_registration_number?: string;
		companies_house_number?: string;
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

	interface FraudRules {
		round_amount_enabled: boolean;
		future_date_enabled: boolean;
		bank_change_enabled: boolean;
		stat_anomaly_enabled: boolean;
		rush_payment_enabled: boolean;
		new_vendor_large_enabled: boolean;
		personal_email_enabled: boolean;
		llm_anomaly_enabled: boolean;
		round_amount_min: string;
		rush_payment_max_days: number;
		new_vendor_max_age_days: number;
		new_vendor_large_amount: string;
		stat_anomaly_sigma: number;
		stat_anomaly_min_history: number;
		personal_email_domains: string[];
	}

	interface OrgSettings {
		company: CompanyProfile;
		invoice_defaults: InvoiceDefaults;
		erp?: ErpConfig;
		fraud_rules?: Partial<FraudRules>;
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
	let vatNumber = $state('');
	let companiesHouseNumber = $state('');
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
	let testingExtraction = $state(false);
	let extractionTestResult = $state<{ success: boolean; message: string } | null>(null);

	async function testExtraction() {
		testingExtraction = true;
		extractionTestResult = null;
		try {
			extractionTestResult = await api.post<{ success: boolean; message: string }>('/api/organization/test-extraction', {
				provider: extractionProgramType === 'platform' ? 'claude_vision' : extractionProvider,
				api_key: extractionApiKey,
				aws_access_key_id: extractionAwsKeyId,
				aws_secret_access_key: extractionAwsSecret,
				aws_region: extractionAwsRegion,
				base_url: extractionOllamaUrl,
				model: extractionOllamaModel,
			});
		} catch (err) {
			extractionTestResult = { success: false, message: err instanceof Error ? err.message : m('org.toast.testFailed') };
		} finally {
			testingExtraction = false;
		}
	}

	const EXTRACTION_PROVIDERS = [
		{ value: 'claude_vision', label: 'Claude Vision (Anthropic)' },
		{ value: 'openai_vision', label: 'GPT-4V (OpenAI)' },
		{ value: 'aws_textract', label: 'AWS Textract' },
		{ value: 'ollama', label: 'Ollama (Local)' },
	];
	let extractionOllamaUrl = $state('http://localhost:11434');
	let extractionOllamaModel = $state('llama3.2-vision:11b');
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
	// Data sync
	let syncingGL = $state(false);
	let syncingPOs = $state(false);
	let glSyncResult = $state('');
	let poSyncResult = $state('');

	async function syncGLAccounts() {
		syncingGL = true;
		glSyncResult = '';
		try {
			const result = await api.post<{ message: string }>('/api/gl-accounts/sync-erp', {});
			glSyncResult = result.message;
		} catch (err) {
			glSyncResult = err instanceof Error ? err.message : 'Sync failed';
		} finally {
			syncingGL = false;
		}
	}

	async function syncPurchaseOrders() {
		syncingPOs = true;
		poSyncResult = '';
		try {
			const result = await api.post<{ message: string }>('/api/purchase-orders/sync-erp', {});
			poSyncResult = result.message;
		} catch (err) {
			poSyncResult = err instanceof Error ? err.message : 'Sync failed';
		} finally {
			syncingPOs = false;
		}
	}

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
			connectionResult = { success: false, message: err instanceof Error ? err.message : m('org.toast.testFailed') };
		} finally {
			testingConnection = false;
		}
	}

	$effect(() => {
		loadOrg();
		loadCustomDomains();
		loadResidency();
		loadChat();
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
			vatNumber = data.settings.company.vat_registration_number ?? '';
			companiesHouseNumber = data.settings.company.companies_house_number ?? '';
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
			// Security (MFA enforcement)
			const mfaCfg = (data.settings as unknown as Record<string, unknown>).mfa as
				| Record<string, unknown>
				| undefined;
			mfaRequired = (mfaCfg?.required as boolean) ?? false;

			// Fraud rules — fetch the canonical defaults once, then layer
			// any org overrides on top. Both are stored so the Reset button
			// has something to revert to.
			if (!fraudDefaults) {
				try {
					fraudDefaults = await api.get<FraudRules>(
						'/api/organization/fraud-rules/defaults'
					);
				} catch {
					/* admin-only; non-critical for non-admin viewers */
				}
			}
			if (fraudDefaults) {
				const overrides =
					((data.settings as unknown as Record<string, unknown>).fraud_rules as
						| Partial<FraudRules>
						| undefined) ?? {};
				fraud = { ...fraudDefaults, ...overrides };
				personalEmailDomainsText = fraud.personal_email_domains.join('\n');
			}
			// Payments
			const pmt = (data.settings as unknown as Record<string, unknown>).payments as
				| Record<string, unknown>
				| undefined;
			if (pmt) {
				paymentsProvider = (pmt.provider as string) || 'mock';
				paymentsProgramType = (pmt.program_type as string) || 'byok';
				paymentsApiKey = (pmt.api_key as string) || '';
				paymentsOrgId = (pmt.org_id as string) || '';
				paymentsOriginatingAccount = (pmt.originating_account_id as string) || '';
				paymentsWebhookSecret = (pmt.webhook_secret as string) || '';
				paymentsSandbox = (pmt.sandbox as boolean) ?? true;
				paymentsCfoThreshold = (pmt.cfo_approval_above as number | null) ?? null;
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
				extractionOllamaUrl = (extraction.base_url as string) || 'http://localhost:11434';
				extractionOllamaModel = (extraction.model as string) || 'llama3.2-vision:11b';
			}
			// Branding (white-label)
			const brandCfg = (data.settings as unknown as Record<string, unknown>).brand as
				| Record<string, unknown>
				| undefined;
			if (brandCfg) {
				brandProductName = (brandCfg.product_name as string) || '';
				brandLogoUrl = (brandCfg.logo_url as string) || '';
				brandAccentColor = (brandCfg.accent_color as string) || '';
				brandAccentStrongColor = (brandCfg.accent_strong_color as string) || '';
				brandSupportUrl = (brandCfg.support_url as string) || '';
				brandLegalUrl = (brandCfg.legal_url as string) || '';
			}
		} catch {
			toast(m('org.toast.loadFailed'), 'error');
		}
	}

	let savingProfile = $state(false);
	let savingDefaults = $state(false);
	let savingErp = $state(false);

	// Security
	let mfaRequired = $state(false);
	let savingSecurity = $state(false);
	// Advisory, computed server-side on every read (never persisted) — whether
	// "require MFA" is actually enforced right now, or a silent no-op because
	// the platform master switch (FEOH_MFA_ENABLED) is off. Derived off `org`
	// (not a plain load-time assignment) so it stays correct after
	// `saveSecurity()` replaces `org` with the PATCH response too. See
	// backend/app/api/organization.py's `_org_response`.
	let mfaEnforcementActive = $derived(
		((org?.settings as unknown as Record<string, unknown> | undefined)?.mfa as
			| Record<string, unknown>
			| undefined)?.enforcement_active === true
	);

	// Fraud detection — defaults loaded once from the backend; the form
	// reflects (defaults ⊕ org overrides) so a stale UI can't drift from
	// what the warning engine actually evaluates.
	let fraudDefaults = $state<FraudRules | null>(null);
	let fraud = $state<FraudRules | null>(null);
	let personalEmailDomainsText = $state('');
	let savingFraud = $state(false);

	// Payments
	let paymentsProvider = $state('mock');
	let paymentsProgramType = $state('byok'); // mock = no key; modern_treasury = byok keys
	let paymentsApiKey = $state('');
	let paymentsOrgId = $state('');
	let paymentsOriginatingAccount = $state('');
	let paymentsWebhookSecret = $state('');
	let paymentsSandbox = $state(true);
	let paymentsCfoThreshold = $state<number | null>(null);
	let savingPayments = $state(false);
	let testingPayments = $state(false);
	let paymentsTestResult = $state<{ success: boolean; message: string } | null>(null);

	async function testPayments() {
		testingPayments = true;
		paymentsTestResult = null;
		try {
			paymentsTestResult = await api.post<{ success: boolean; message: string }>(
				'/api/organization/test-payments',
				{
					provider: paymentsProvider,
					api_key: paymentsApiKey,
					org_id: paymentsOrgId,
					originating_account_id: paymentsOriginatingAccount,
					sandbox: paymentsSandbox,
				}
			);
		} catch (err) {
			paymentsTestResult = {
				success: false,
				message: err instanceof Error ? err.message : m('org.toast.testFailed'),
			};
		} finally {
			testingPayments = false;
		}
	}

	async function patchSettings(section: string, partial: Record<string, unknown>) {
		const data = await api.patch<OrgResponse>('/api/organization', {
			...(partial.company ? { name: name.trim() } : {}),
			settings: partial,
		});
		org = data;
		toast(m('org.toast.sectionSaved', { section }), 'success');
	}

	async function saveProfile() {
		savingProfile = true;
		try {
			await patchSettings(m('org.section.companySaved'), {
				company: {
					address, phone, website,
					tax_id: taxId,
					vat_registration_number: vatNumber,
					companies_house_number: companiesHouseNumber,
					logo_url: org?.settings.company.logo_url ?? '',
				},
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.toast.saveFailed'), 'error');
		} finally {
			savingProfile = false;
		}
	}

	async function saveDefaults() {
		savingDefaults = true;
		try {
			await patchSettings(m('org.section.defaultsSaved'), {
				invoice_defaults: {
					currency,
					payment_terms: paymentTerms,
					number_prefix: numberPrefix,
					default_gl_account: defaultGl,
					default_cost_center: defaultCostCenter,
				},
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.toast.saveFailed'), 'error');
		} finally {
			savingDefaults = false;
		}
	}

	async function saveErp() {
		savingErp = true;
		try {
			await patchSettings(m('org.section.erpSaved'), {
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
			toast(err instanceof Error ? err.message : m('org.toast.saveFailed'), 'error');
		} finally {
			savingErp = false;
		}
	}

	async function saveExtraction() {
		savingExtraction = true;
		try {
			await patchSettings(m('org.section.extractionSaved'), {
				extraction: {
					program_type: extractionProgramType,
					provider: extractionProvider,
					api_key: extractionApiKey,
					aws_access_key_id: extractionAwsKeyId,
					aws_secret_access_key: extractionAwsSecret,
					aws_region: extractionAwsRegion,
					base_url: extractionOllamaUrl,
					model: extractionOllamaModel,
				},
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.toast.saveFailed'), 'error');
		} finally {
			savingExtraction = false;
		}
	}

	async function saveCards() {
		savingCards = true;
		try {
			await patchSettings(m('org.section.cardsSaved'), {
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
			toast(err instanceof Error ? err.message : m('org.toast.saveFailed'), 'error');
		} finally {
			savingCards = false;
		}
	}

	async function saveSecurity() {
		savingSecurity = true;
		try {
			await patchSettings(m('org.section.securitySaved'), {
				mfa: { required: mfaRequired },
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.toast.saveFailed'), 'error');
		} finally {
			savingSecurity = false;
		}
	}

	async function saveFraud() {
		if (!fraud) return;
		savingFraud = true;
		try {
			const domains = personalEmailDomainsText
				.split(/[\n,]+/)
				.map((d) => d.trim().toLowerCase())
				.filter((d) => d.length > 0);
			const payload: FraudRules = { ...fraud, personal_email_domains: domains };
			await patchSettings(m('org.section.fraudSaved'), { fraud_rules: payload });
			fraud = payload;
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.toast.saveFailed'), 'error');
		} finally {
			savingFraud = false;
		}
	}

	function resetFraudToDefaults() {
		if (!fraudDefaults) return;
		fraud = { ...fraudDefaults };
		personalEmailDomainsText = fraudDefaults.personal_email_domains.join('\n');
	}

	async function savePayments() {
		savingPayments = true;
		try {
			await patchSettings(m('org.section.paymentsSaved'), {
				payments: {
					provider: paymentsProvider,
					program_type: paymentsProgramType,
					api_key: paymentsApiKey,
					org_id: paymentsOrgId,
					originating_account_id: paymentsOriginatingAccount,
					webhook_secret: paymentsWebhookSecret,
					sandbox: paymentsSandbox,
					cfo_approval_above: paymentsCfoThreshold,
				},
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.toast.saveFailed'), 'error');
		} finally {
			savingPayments = false;
		}
	}

	const planLabel = (plan: string): string =>
		({
			free: m('org.plan.free'),
			pro: m('org.plan.pro'),
			enterprise: m('org.plan.enterprise'),
		})[plan] ?? plan;

	// ── White-label branding ────────────────────────────────────────────
	import { brand } from '$lib/stores/brand.svelte';
	import { accentStrongContrast } from '$lib/stores/brandTheme';
	import { formatRatio, WCAG_AA_NORMAL } from '$lib/a11y/contrast';
	import FieldWarning from '$lib/components/ui/FieldWarning.svelte';

	let brandProductName = $state('');
	let brandLogoUrl = $state('');
	let brandAccentColor = $state('');
	let brandAccentStrongColor = $state('');
	let brandSupportUrl = $state('');
	let brandLegalUrl = $state('');
	let savingBranding = $state(false);

	const HEX_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
	// Default accent tokens (mirror src/app.css :root) so the color pickers show
	// the live default when the org hasn't set one — without writing it back.
	const DEFAULT_ACCENT = '#638cff';
	const DEFAULT_ACCENT_STRONG = '#3f5fd6';

	// The strong accent is written straight into the `--accent-strong` custom
	// property, whose one contract is that white text sits on it. Warn while
	// the field is still being edited; the backend accepts any valid hex and
	// the brand is the tenant's call, so this advises rather than blocks.
	const accentStrongRatio = $derived(accentStrongContrast(brandAccentStrongColor));
	const accentStrongFails = $derived(
		accentStrongRatio !== null && accentStrongRatio < WCAG_AA_NORMAL
	);

	async function saveBranding() {
		// Client-side validation mirroring the backend BrandConfig guards, so a
		// typo surfaces inline instead of as a 422.
		if (brandAccentColor.trim() && !HEX_RE.test(brandAccentColor.trim())) {
			toast(m('org.branding.toast.accentInvalid'), 'error');
			return;
		}
		if (brandAccentStrongColor.trim() && !HEX_RE.test(brandAccentStrongColor.trim())) {
			toast(m('org.branding.toast.accentStrongInvalid'), 'error');
			return;
		}
		for (const [label, val] of [
			[m('org.branding.label.logoUrl'), brandLogoUrl],
			[m('org.branding.label.supportUrl'), brandSupportUrl],
			[m('org.branding.label.legalUrl'), brandLegalUrl],
		] as const) {
			if (val.trim() && !/^https?:\/\//i.test(val.trim())) {
				toast(m('org.branding.toast.urlInvalid', { label }), 'error');
				return;
			}
		}
		savingBranding = true;
		try {
			await api.put('/api/organization/branding', {
				product_name: brandProductName.trim(),
				logo_url: brandLogoUrl.trim(),
				accent_color: brandAccentColor.trim(),
				accent_strong_color: brandAccentStrongColor.trim(),
				support_url: brandSupportUrl.trim(),
				legal_url: brandLegalUrl.trim(),
			});
			// Refresh the live brand store so the sidebar logo/name + theme update
			// without a reload.
			brand.reset();
			await brand.ensureLoadedAndApply();
			toast(m('org.branding.toast.saved'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.toast.saveFailed'), 'error');
		} finally {
			savingBranding = false;
		}
	}

	// ── Custom domains (white-label vanity hostnames) ───────────────────
	// Manages settings.brand.custom_domains — the list the backend resolver
	// matches an inbound Host against (with the JWT org-claim cross-check still
	// gating access). See docs/white-label.md § Custom domains.
	let customDomains = $state<string[]>([]);
	let newDomain = $state('');
	let loadingDomains = $state(true);
	let domainsError = $state('');
	let savingDomains = $state(false);
	// The host being removed is armed for a confirm-on-second-click.
	let confirmRemoveDomain = $state<string | null>(null);

	// Mirror of the backend normalize_custom_domain: bare, lowercase hostname,
	// no scheme / path / port / spaces. Returns null when there's nothing usable.
	function normalizeDomain(raw: string): string | null {
		let h = (raw || '').trim().toLowerCase();
		if (!h) return null;
		if (h.startsWith('[')) return null; // IPv6 literal — never a custom domain
		// Strip a scheme if the user pasted a full URL.
		h = h.replace(/^https?:\/\//, '');
		// Drop a path/query and a :port suffix.
		h = h.split('/')[0].split('?')[0].split(':')[0];
		if (!h || h.includes(' ')) return null;
		// UX guard, intentionally STRICTER than the backend: require a dotted
		// hostname (label.label…), since a real vanity domain always has a TLD.
		// The backend `normalize_custom_domain` is the authority and accepts a
		// bare single-label host too; this only spares the operator an obvious
		// typo client-side (the safe direction — backend still validates).
		if (!/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/.test(h)) {
			return null;
		}
		return h;
	}

	async function loadCustomDomains() {
		loadingDomains = true;
		domainsError = '';
		try {
			const data = await api.get<{ custom_domains: string[] }>(
				'/api/organization/branding/custom-domains'
			);
			customDomains = data.custom_domains ?? [];
		} catch (err) {
			domainsError = err instanceof Error ? err.message : m('org.customDomains.toast.loadFailed');
		} finally {
			loadingDomains = false;
		}
	}

	// PUT replaces the whole list (the backend endpoint is a full replace),
	// re-reads the normalized result, and refreshes local state.
	async function saveCustomDomains(next: string[]) {
		savingDomains = true;
		try {
			const data = await api.put<{ custom_domains: string[] }>(
				'/api/organization/branding/custom-domains',
				{ custom_domains: next }
			);
			customDomains = data.custom_domains ?? [];
			return true;
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.customDomains.toast.saveFailed'), 'error');
			return false;
		} finally {
			savingDomains = false;
		}
	}

	async function addCustomDomain() {
		const host = normalizeDomain(newDomain);
		if (!host) {
			toast(m('org.customDomains.toast.invalid'), 'error');
			return;
		}
		if (customDomains.includes(host)) {
			toast(m('org.customDomains.toast.duplicate'), 'error');
			return;
		}
		const ok = await saveCustomDomains([...customDomains, host]);
		if (ok) {
			newDomain = '';
			toast(m('org.customDomains.toast.added'), 'success');
		}
	}

	// ── Data residency (GDPR/CCPA region pin) ───────────────────────────
	// Manages settings.residency.region plus the backend's advisory
	// configured-vs-deployed `alignment` verdict — which is the whole point of
	// showing this here: the pin is a commitment, and an admin should be able to
	// see whether the platform is physically honouring it yet. Nothing on this
	// panel blocks; the region never moves data by itself.
	// See docs/data-residency.md.
	interface ResidencyAlignment {
		status: string; // "aligned" | "misaligned" | "unknown"
		aligned: boolean | null; // null ⇔ status "unknown" — never read as yes
		deployed_region: string | null;
		reason: string | null;
	}
	interface ResidencyResponse {
		region: string;
		default_region: string;
		supported_regions: string[];
		placement: Record<string, string>;
		alignment: ResidencyAlignment;
	}

	// Region tokens come from the server; their display names are ours.
	const REGION_LABEL_KEYS: Record<string, MessageKey> = {
		us: 'org.residency.region.us',
		eu: 'org.residency.region.eu',
		uk: 'org.residency.region.uk',
		ca: 'org.residency.region.ca',
		au: 'org.residency.region.au'
	};

	let residencyRegion = $state(''); // the select's bound value
	let residencySavedRegion = $state(''); // last persisted effective region
	let residencyDefault = $state('');
	let residencyRegions = $state<string[]>([]);
	let residencyPlacement = $state<Record<string, string>>({});
	let residencyAlignment = $state<ResidencyAlignment | null>(null);
	let loadingResidency = $state(true);
	let residencyError = $state('');
	let savingResidency = $state(false);

	// An unmapped token renders as itself rather than vanishing — the server
	// owns the supported set, so a region added there stays selectable here.
	function regionLabel(token: string | null): string {
		if (!token) return '';
		const key = REGION_LABEL_KEYS[token];
		return key ? m(key) : token.toUpperCase();
	}

	function applyResidency(data: ResidencyResponse) {
		residencyRegion = data.region;
		residencySavedRegion = data.region;
		residencyDefault = data.default_region;
		residencyRegions = data.supported_regions ?? [];
		residencyPlacement = data.placement ?? {};
		residencyAlignment = data.alignment ?? null;
	}

	async function loadResidency() {
		loadingResidency = true;
		residencyError = '';
		try {
			applyResidency(await api.get<ResidencyResponse>('/api/organization/data-residency'));
		} catch (err) {
			residencyError = err instanceof Error ? err.message : m('org.residency.toast.loadFailed');
		} finally {
			loadingResidency = false;
		}
	}

	// The PUT answers with the same payload as the GET, alignment included, so
	// the verdict for the region just pinned lands without a second round trip.
	async function saveResidency() {
		savingResidency = true;
		try {
			applyResidency(
				await api.put<ResidencyResponse>('/api/organization/data-residency', {
					region: residencyRegion
				})
			);
			toast(m('org.residency.toast.saved'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.residency.toast.saveFailed'), 'error');
			// Snap the control back to what is actually persisted, so the panel
			// never shows a region the tenant is not pinned to.
			residencyRegion = residencySavedRegion;
		} finally {
			savingResidency = false;
		}
	}

	const alignmentMessage = $derived.by(() => {
		const a = residencyAlignment;
		if (!a) return '';
		if (a.status === 'aligned') {
			return m('org.residency.alignment.aligned', { region: regionLabel(a.deployed_region) });
		}
		if (a.status === 'misaligned') {
			return m('org.residency.alignment.misaligned', {
				configured: regionLabel(residencySavedRegion),
				deployed: regionLabel(a.deployed_region)
			});
		}
		return a.reason === 'deployed_region_unrecognised'
			? m('org.residency.alignment.unknownUnrecognised')
			: m('org.residency.alignment.unknownUnset');
	});

	async function removeCustomDomain(host: string) {
		// Two-click arm/confirm so a stray click can't drop a live domain.
		if (confirmRemoveDomain !== host) {
			confirmRemoveDomain = host;
			return;
		}
		confirmRemoveDomain = null;
		const ok = await saveCustomDomains(customDomains.filter((d) => d !== host));
		if (ok) toast(m('org.customDomains.toast.removed'), 'success');
	}

	// ── Chat notifications (Slack / Teams) ──────────────────────────────
	// The incoming-webhook URL is the credential for both real providers, and
	// it is WRITE-ONLY end to end: no endpoint returns it, so there is no
	// `chatWebhookUrl` mirror of the persisted value here — only the status the
	// server reports (configured yes/no + the bare host it posts to) and the
	// draft the admin is currently typing. Don't add one.
	// See backend/docs/notifications.md § Rotating the webhook URL.
	let chat = $state<ChatNotificationStatus | null>(null);
	let chatEnabled = $state(false);
	let chatProvider = $state('mock');
	let chatEvents = $state<Record<string, boolean>>({});
	let loadingChat = $state(true);
	let chatError = $state('');
	let savingChat = $state(false);
	let newChatWebhook = $state('');
	let savingChatWebhook = $state(false);
	let confirmRemoveChatWebhook = $state(false);

	function applyChat(data: ChatNotificationStatus) {
		chat = data;
		chatEnabled = data.enabled;
		chatProvider = data.provider ?? 'mock';
		// A missing per-event key means "on" (the backend's opt-out default), so
		// materialize the full map here rather than letting an unchecked box
		// mean "unset".
		chatEvents = Object.fromEntries(
			(data.supported_events ?? []).map((e) => [e, data.events?.[e] ?? true])
		);
	}

	function chatProviderLabel(token: string): string {
		return CHAT_PROVIDER_LABELS[token] ?? token;
	}

	function chatEventLabel(token: string): string {
		return CHAT_EVENT_LABELS[token] ?? token;
	}

	// Chat on, a real provider selected, no webhook stored → the adapter fails
	// closed and silently posts nothing. Surface that rather than let the panel
	// read as configured.
	const chatWebhookMissing = $derived(
		!!chat && chat.enabled && chat.provider !== 'mock' && !chat.webhook_configured
	);

	async function loadChat() {
		loadingChat = true;
		chatError = '';
		try {
			applyChat(await getChatNotifications());
		} catch (err) {
			chatError = err instanceof Error ? err.message : m('org.chat.toast.loadFailed');
		} finally {
			loadingChat = false;
		}
	}

	async function saveChat() {
		savingChat = true;
		try {
			// The response is authoritative — in particular it re-reports
			// `webhook_configured`, which this save deliberately does not touch.
			applyChat(
				await updateChatNotifications({
					enabled: chatEnabled,
					provider: chatProvider,
					events: chatEvents
				})
			);
			toast(m('org.chat.toast.saved'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.chat.toast.saveFailed'), 'error');
		} finally {
			savingChat = false;
		}
	}

	async function saveChatWebhook() {
		const url = newChatWebhook.trim();
		if (!url) {
			toast(m('org.chat.webhook.toast.empty'), 'error');
			return;
		}
		savingChatWebhook = true;
		try {
			applyChat(await rotateChatWebhook(url));
			// Drop the credential from component state the moment it is stored —
			// it is never re-fetchable, so keeping it around buys nothing.
			newChatWebhook = '';
			toast(m('org.chat.webhook.toast.saved'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('org.chat.webhook.toast.saveFailed'), 'error');
		} finally {
			savingChatWebhook = false;
		}
	}

	async function removeChatWebhook() {
		// Two-click arm/confirm — revoking silently stops every approval post.
		if (!confirmRemoveChatWebhook) {
			confirmRemoveChatWebhook = true;
			return;
		}
		confirmRemoveChatWebhook = false;
		savingChatWebhook = true;
		try {
			applyChat(await revokeChatWebhook());
			toast(m('org.chat.webhook.toast.removed'), 'success');
		} catch (err) {
			toast(
				err instanceof Error ? err.message : m('org.chat.webhook.toast.removeFailed'),
				'error'
			);
		} finally {
			savingChatWebhook = false;
		}
	}
</script>

<svelte:window
	onclick={(e) => {
		// Un-arm a pending domain-remove confirm when clicking elsewhere.
		if (confirmRemoveDomain && !(e.target as HTMLElement)?.closest?.('.domain-remove')) {
			confirmRemoveDomain = null;
		}
		// Same for the chat-webhook revoke.
		if (
			confirmRemoveChatWebhook &&
			!(e.target as HTMLElement)?.closest?.('.chat-webhook-remove')
		) {
			confirmRemoveChatWebhook = false;
		}
	}}
/>

<PageHeader title={m('org.title')}>
	{#if org}
		<div class="sections">
			<section class="getting-started card">
				<h2>{m('org.gettingStarted.title')}</h2>
				<p class="card-hint">{m('org.gettingStarted.intro')}</p>
				<nav class="gs-links" aria-label={m('org.gettingStarted.title')}>
					<a href="#org-company">{m('org.gettingStarted.company')}</a>
					<a href="#org-defaults">{m('org.gettingStarted.defaults')}</a>
					<a href="/admin">{m('org.gettingStarted.users')}</a>
					<a href="#org-payments">{m('org.gettingStarted.approvals')}</a>
					<a href="#org-branding">{m('org.gettingStarted.branding')}</a>
				</nav>
			</section>

			<section class="card" id="org-company">
				<h2>{m('org.section.company')}</h2>
				<div class="form-grid">
					<label>
						<span>{m('org.company.name')}</span>
						<input type="text" bind:value={name} />
					</label>
					<label>
						<span>{m('org.company.taxId')}</span>
						<input type="text" bind:value={taxId} placeholder={m('org.company.taxIdPlaceholder')} />
					</label>
					<label>
						<span>{m('org.company.vatNumber')}</span>
						<input type="text" bind:value={vatNumber} />
					</label>
					<label>
						<span>{m('org.company.companiesHouseNumber')}</span>
						<input type="text" bind:value={companiesHouseNumber} />
					</label>
					<label class="full-width">
						<span>{m('org.company.address')}</span>
						<textarea bind:value={address} rows="2" placeholder={m('org.company.addressPlaceholder')}></textarea>
					</label>
					<label>
						<span>{m('org.company.phone')}</span>
						<input type="tel" bind:value={phone} />
					</label>
					<label>
						<span>{m('org.company.website')}</span>
						<input type="url" bind:value={website} placeholder="https://" />
					</label>
				</div>
				<div class="section-footer">
					<button class="btn-save-section" disabled={savingProfile} onclick={saveProfile}>
						{savingProfile ? m('org.common.saving') : m('org.company.save')}
					</button>
				</div>
			</section>

			<section class="card" id="org-defaults">
				<h2>{m('org.section.defaults')}</h2>
				<p class="card-hint">{m('org.defaults.hint')}</p>
				<div class="form-grid">
					<label>
						<span>{m('org.defaults.currency')}</span>
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
						<span>{m('org.defaults.paymentTerms')}</span>
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
						<span>{m('org.defaults.numberPrefix')}</span>
						<input type="text" bind:value={numberPrefix} placeholder="INV-" />
					</label>
					<label>
						<span>{m('org.defaults.defaultGl')}</span>
						<input type="text" bind:value={defaultGl} placeholder={m('org.defaults.defaultGlPlaceholder')} />
					</label>
					<label>
						<span>{m('org.defaults.defaultCostCenter')}</span>
						<input type="text" bind:value={defaultCostCenter} placeholder={m('org.defaults.defaultCostCenterPlaceholder')} />
					</label>
				</div>
				<div class="section-footer">
					<button class="btn-save-section" disabled={savingDefaults} onclick={saveDefaults}>
						{savingDefaults ? m('org.common.saving') : m('org.defaults.save')}
					</button>
				</div>
			</section>

			<section class="card" id="org-branding">
				<h2>{m('org.section.branding')}</h2>
				<p class="card-hint">
					{m('org.branding.hint')}
				</p>
				<div class="form-grid">
					<label>
						<span>{m('org.branding.productName')}</span>
						<input
							type="text"
							bind:value={brandProductName}
							placeholder={m('org.branding.productNamePlaceholder')}
							maxlength="120"
						/>
					</label>
					<label>
						<span>{m('org.branding.logoUrl')}</span>
						<input
							type="url"
							bind:value={brandLogoUrl}
							placeholder={m('org.branding.logoUrlPlaceholder')}
						/>
					</label>
					<label>
						<span>{m('org.branding.accentColor')}</span>
						<span class="color-field">
							<input
								type="color"
								aria-label={m('org.branding.accentColorPicker')}
								value={brandAccentColor.trim() || DEFAULT_ACCENT}
								oninput={(e) => (brandAccentColor = e.currentTarget.value)}
							/>
							<input
								type="text"
								bind:value={brandAccentColor}
								placeholder={DEFAULT_ACCENT}
							/>
						</span>
					</label>
					<label>
						<span>{m('org.branding.accentStrong')}</span>
						<span class="color-field">
							<input
								type="color"
								aria-label={m('org.branding.accentStrongPicker')}
								value={brandAccentStrongColor.trim() || DEFAULT_ACCENT_STRONG}
								oninput={(e) => (brandAccentStrongColor = e.currentTarget.value)}
							/>
							<input
								type="text"
								bind:value={brandAccentStrongColor}
								placeholder={DEFAULT_ACCENT_STRONG}
							/>
						</span>
						<FieldWarning
							show={accentStrongFails}
							testId="accent-strong-contrast-warning"
							message={m('common.contrastWarning', {
								ratio: accentStrongRatio === null ? '' : formatRatio(accentStrongRatio)
							})}
						/>
					</label>
					<label>
						<span>{m('org.branding.supportUrl')}</span>
						<input
							type="url"
							bind:value={brandSupportUrl}
							placeholder={m('org.branding.supportUrlPlaceholder')}
						/>
					</label>
					<label>
						<span>{m('org.branding.legalUrl')}</span>
						<input
							type="url"
							bind:value={brandLegalUrl}
							placeholder={m('org.branding.legalUrlPlaceholder')}
						/>
					</label>
				</div>
				<p class="card-hint">
					{m('org.branding.strongHint')}
				</p>
				<div class="section-footer">
					<button class="btn-save-section" disabled={savingBranding} onclick={saveBranding}>
						{savingBranding ? m('org.common.saving') : m('org.branding.save')}
					</button>
				</div>
			</section>

			<section class="card">
				<h2>{m('org.section.customDomains')}</h2>
				<p class="card-hint">
					{m('org.customDomains.hint', { example: 'ap.acmecorp.com', slug: org?.slug ?? 'tenant' })}
				</p>

				{#if loadingDomains}
					<p class="card-hint">{m('org.customDomains.loading')}</p>
				{:else if domainsError}
					<p class="domain-error" role="alert">{domainsError}</p>
				{:else}
					{#if customDomains.length === 0}
						<p class="card-hint domain-empty">{m('org.customDomains.empty')}</p>
					{:else}
						<ul class="domain-list">
							{#each customDomains as host (host)}
								<li class="domain-row">
									<span class="domain-name mono">{host}</span>
									<span class="domain-remove">
										<button
											type="button"
											class="btn-remove-domain"
											class:armed={confirmRemoveDomain === host}
											disabled={savingDomains}
											aria-label={m('org.customDomains.removeAria', { host })}
											onclick={() => removeCustomDomain(host)}
										>
											{confirmRemoveDomain === host ? m('org.customDomains.confirmRemove') : m('org.customDomains.remove')}
										</button>
									</span>
								</li>
							{/each}
						</ul>
					{/if}

					<form
						class="domain-add"
						onsubmit={(e) => {
							e.preventDefault();
							addCustomDomain();
						}}
					>
						<input
							type="text"
							bind:value={newDomain}
							placeholder={m('org.customDomains.newPlaceholder')}
							aria-label={m('org.customDomains.newAria')}
							autocomplete="off"
							spellcheck="false"
						/>
						<button type="submit" class="btn-save-section" disabled={savingDomains}>
							{savingDomains ? m('org.customDomains.adding') : m('org.customDomains.add')}
						</button>
					</form>
				{/if}
			</section>

			<section class="card">
				<h2>{m('org.section.chat')}</h2>
				<p class="card-hint">{m('org.chat.hint')}</p>

				{#if loadingChat}
					<p class="card-hint">{m('org.chat.loading')}</p>
				{:else if chatError}
					<p class="chat-error" role="alert">{chatError}</p>
				{:else if chat}
					<div class="form-grid">
						<label class="switch-row">
							<input type="checkbox" bind:checked={chatEnabled} />
							<span>{m('org.chat.enabled')}</span>
						</label>
						<label>
							{m('org.chat.provider')}
							<select bind:value={chatProvider}>
								{#each chat.supported_providers as p (p)}
									<option value={p}>{chatProviderLabel(p)}</option>
								{/each}
							</select>
						</label>
					</div>

					<fieldset class="chat-events">
						<legend>{m('org.chat.events')}</legend>
						<p class="card-hint">{m('org.chat.eventsHint')}</p>
						{#each chat.supported_events as ev (ev)}
							<label class="switch-row">
								<input
									type="checkbox"
									checked={chatEvents[ev] ?? true}
									onchange={(e) =>
										(chatEvents = {
											...chatEvents,
											[ev]: (e.currentTarget as HTMLInputElement).checked
										})}
								/>
								<span>{chatEventLabel(ev)}</span>
							</label>
						{/each}
					</fieldset>

					<div class="section-footer">
						<button class="btn-save-section" disabled={savingChat} onclick={saveChat}>
							{savingChat ? m('org.common.saving') : m('org.chat.save')}
						</button>
					</div>

					<h3 class="chat-subhead">{m('org.chat.webhook.title')}</h3>
					<p class="card-hint">{m('org.chat.webhook.hint')}</p>

					{#if chatWebhookMissing}
						<p class="chat-warning" role="alert">
							{m('org.chat.webhook.missingWarning', {
								provider: chatProviderLabel(chat.provider ?? '')
							})}
						</p>
					{/if}

					<div class="chat-webhook-status">
						{#if chat.webhook_configured}
							<span class="chat-webhook-set">
								{chat.webhook_host
									? m('org.chat.webhook.configured', { host: chat.webhook_host })
									: m('org.chat.webhook.configuredUnknownHost')}
							</span>
							<span class="chat-webhook-remove">
								<button
									type="button"
									class="btn-remove-domain"
									class:armed={confirmRemoveChatWebhook}
									disabled={savingChatWebhook}
									aria-label={m('org.chat.webhook.removeAria')}
									onclick={removeChatWebhook}
								>
									{confirmRemoveChatWebhook
										? m('org.chat.webhook.confirmRemove')
										: m('org.chat.webhook.remove')}
								</button>
							</span>
						{:else}
							<span class="card-hint">{m('org.chat.webhook.notConfigured')}</span>
						{/if}
					</div>

					<form
						class="domain-add"
						onsubmit={(e) => {
							e.preventDefault();
							saveChatWebhook();
						}}
					>
						<input
							type="text"
							bind:value={newChatWebhook}
							placeholder={m('org.chat.webhook.placeholder')}
							aria-label={m('org.chat.webhook.inputAria')}
							autocomplete="off"
							spellcheck="false"
						/>
						<button type="submit" class="btn-save-section" disabled={savingChatWebhook}>
							{savingChatWebhook
								? m('org.chat.webhook.saving')
								: chat.webhook_configured
									? m('org.chat.webhook.replace')
									: m('org.chat.webhook.set')}
						</button>
					</form>
					<p class="card-hint">{m('org.chat.webhook.rotateHint')}</p>
				{/if}
			</section>

			<section class="card">
				<h2>{m('org.section.dataResidency')}</h2>
				<p class="card-hint">{m('org.residency.hint')}</p>

				{#if loadingResidency}
					<p class="card-hint">{m('org.residency.loading')}</p>
				{:else if residencyError}
					<p class="residency-error" role="alert">{residencyError}</p>
				{:else}
					<div class="form-grid">
						<label>
							<span>{m('org.residency.regionLabel')}</span>
							<select bind:value={residencyRegion}>
								{#each residencyRegions as token (token)}
									<option value={token}>
										{token === residencyDefault
											? m('org.residency.regionDefault', { region: regionLabel(token) })
											: regionLabel(token)}
									</option>
								{/each}
							</select>
						</label>
					</div>

					{#if residencyPlacement.db_cluster}
						<p class="card-hint residency-placement">
							{m('org.residency.placement', {
								cluster: residencyPlacement.db_cluster,
								bucket: residencyPlacement.s3_bucket ?? ''
							})}
						</p>
					{/if}

					{#if residencyAlignment}
						<div
							class="residency-alignment"
							class:ok={residencyAlignment.status === 'aligned'}
							class:warn={residencyAlignment.status === 'misaligned'}
						>
							<strong>{m('org.residency.alignment.title')}</strong>
							<p>{alignmentMessage}</p>
							<p class="residency-advisory">{m('org.residency.alignment.advisory')}</p>
						</div>
					{/if}

					<div class="section-footer">
						<button
							class="btn-save-section"
							disabled={savingResidency || residencyRegion === residencySavedRegion}
							onclick={saveResidency}
						>
							{savingResidency ? m('org.common.saving') : m('org.residency.save')}
						</button>
					</div>
				{/if}
			</section>

			<section class="card">
				<h2>{m('org.section.extraction')}</h2>
				<p class="card-hint">{m('org.extraction.hint')}</p>

				<div class="form-grid">
					<label>
						<span>{m('org.extraction.program')}</span>
						<select bind:value={extractionProgramType}>
							<option value="platform">{m('org.extraction.programPlatform')}</option>
							<option value="byok">{m('org.extraction.programByok')}</option>
						</select>
					</label>
					{#if extractionProgramType === 'byok'}
						<label>
							<span>{m('org.extraction.provider')}</span>
							<select bind:value={extractionProvider}>
								{#each EXTRACTION_PROVIDERS as p}
									<option value={p.value}>{p.label}</option>
								{/each}
							</select>
						</label>
					{:else}
						<label>
							<span>{m('org.extraction.provider')}</span>
							<input type="text" value="Claude Vision (Anthropic)" disabled />
						</label>
					{/if}
				</div>

				{#if extractionProgramType === 'platform'}
					<p class="card-hint" style="margin-top: 10px;">{m('org.extraction.platformHint')}</p>
				{:else if extractionProvider === 'claude_vision' || extractionProvider === 'openai_vision'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>{extractionProvider === 'claude_vision' ? m('org.extraction.anthropicKey') : m('org.extraction.openaiKey')}</span>
							<input type="password" bind:value={extractionApiKey} placeholder="sk-..." />
						</label>
					</div>
				{:else if extractionProvider === 'aws_textract'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>{m('org.extraction.awsKeyId')}</span>
							<input type="text" bind:value={extractionAwsKeyId} />
						</label>
						<label>
							<span>{m('org.extraction.awsSecret')}</span>
							<input type="password" bind:value={extractionAwsSecret} />
						</label>
						<label>
							<span>{m('org.extraction.awsRegion')}</span>
							<input type="text" bind:value={extractionAwsRegion} placeholder="us-east-1" />
						</label>
					</div>
				{:else if extractionProvider === 'ollama'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>{m('org.extraction.ollamaUrl')}</span>
							<input type="url" bind:value={extractionOllamaUrl} placeholder="http://localhost:11434" />
						</label>
						<label>
							<span>{m('org.extraction.model')}</span>
							<select bind:value={extractionOllamaModel}>
								<option value="llama3.2-vision:11b">Llama 3.2 Vision 11B</option>
								<option value="llama3.2-vision:90b">Llama 3.2 Vision 90B</option>
								<option value="llava:13b">LLaVA 13B</option>
								<option value="llava:34b">LLaVA 34B</option>
							</select>
						</label>
					</div>
					<p class="card-hint" style="margin-top: 8px;">{m('org.extraction.ollamaHint')} <code>brew install ollama && ollama pull {extractionOllamaModel}</code></p>
				{/if}

				<div class="erp-test-row">
					<button class="btn-save-section" disabled={savingExtraction} onclick={saveExtraction}>
						{savingExtraction ? m('org.common.saving') : m('org.extraction.save')}
					</button>
					<button class="btn-test" disabled={testingExtraction} onclick={testExtraction}>
						{testingExtraction ? m('org.common.testing') : m('org.common.testConnection')}
					</button>
					{#if extractionTestResult}
						<span class="test-result" class:success={extractionTestResult.success} class:failure={!extractionTestResult.success}>
							{extractionTestResult.message}
						</span>
					{/if}
				</div>
			</section>

			<section class="card">
				<h2>{m('org.section.erp')}</h2>
				<p class="card-hint">{m('org.erp.hint')}</p>
				<div class="form-grid">
					<label>
						<span>{m('org.erp.system')}</span>
						<select bind:value={erpType}>
							{#each ERP_TYPES as erp}
								<option value={erp.value}>{erp.label}</option>
							{/each}
						</select>
					</label>
					<label>
						<span>{m('org.erp.method')}</span>
						<select bind:value={erpMethod}>
							<option value="merge_dev">{m('org.erp.methodMergeDev')}</option>
							<option value="direct">{m('org.erp.methodDirect')}</option>
						</select>
					</label>
				</div>

				{#if erpMethod === 'merge_dev'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>{m('org.erp.mergeApiKey')}</span>
							<input type="password" bind:value={erpApiKey} placeholder="test_..." />
						</label>
						<label>
							<span>{m('org.erp.accountToken')}</span>
							<input type="password" bind:value={erpAccountToken} placeholder={m('org.erp.accountTokenPlaceholder')} />
						</label>
					</div>
					<p class="card-hint" style="margin-top: 8px;">{m('org.erp.mergeHintPre')} <a href="https://app.merge.dev" target="_blank" rel="noopener">{m('org.erp.mergeDashboard')}</a>{m('org.erp.mergeHintPost')}</p>
				{:else if erpType === 'dynamics_365_bc'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>{m('org.erp.baseUrl')}</span>
							<input type="url" bind:value={erpBaseUrl} placeholder="https://api.businesscentral.dynamics.com/v2.0" />
						</label>
						<label>
							<span>{m('org.erp.environment')}</span>
							<input type="text" bind:value={erpEnvironment} placeholder="production" />
						</label>
						<label>
							<span>{m('org.erp.tenantId')}</span>
							<input type="text" bind:value={erpTenantId} />
						</label>
						<label>
							<span>{m('org.erp.clientId')}</span>
							<input type="text" bind:value={erpClientId} />
						</label>
						<label>
							<span>{m('org.erp.clientSecret')}</span>
							<input type="password" bind:value={erpClientSecret} />
						</label>
						<label>
							<span>{m('org.erp.companyId')}</span>
							<input type="text" bind:value={erpCompanyId} />
						</label>
					</div>
				{:else if erpType === 'netsuite'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>{m('org.erp.accountId')}</span>
							<input type="text" bind:value={erpAccountId} placeholder="1234567" />
						</label>
						<label>
							<span>{m('org.erp.consumerKey')}</span>
							<input type="text" bind:value={erpConsumerKey} />
						</label>
						<label>
							<span>{m('org.erp.consumerSecret')}</span>
							<input type="password" bind:value={erpConsumerSecret} />
						</label>
						<label>
							<span>{m('org.erp.tokenId')}</span>
							<input type="text" bind:value={erpTokenId} />
						</label>
						<label>
							<span>{m('org.erp.tokenSecret')}</span>
							<input type="password" bind:value={erpTokenSecret} />
						</label>
					</div>
				{:else}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>{m('org.erp.apiBaseUrl')}</span>
							<input type="url" bind:value={erpBaseUrl} />
						</label>
						<label>
							<span>{m('org.erp.apiKeyClientId')}</span>
							<input type="password" bind:value={erpClientId} />
						</label>
						<label>
							<span>{m('org.erp.apiSecretClientSecret')}</span>
							<input type="password" bind:value={erpClientSecret} />
						</label>
					</div>
					<p class="card-hint" style="margin-top: 8px;">{m('org.erp.directSoonHint', { erp: ERP_TYPES.find(e => e.value === erpType)?.label ?? erpType })}</p>
				{/if}

				<div class="erp-test-row">
					<button class="btn-save-section" disabled={savingErp} onclick={saveErp}>
						{savingErp ? m('org.common.saving') : m('org.erp.save')}
					</button>
					<button class="btn-test" disabled={testingConnection} onclick={testConnection}>
						{testingConnection ? m('org.common.testing') : m('org.common.testConnection')}
					</button>
					{#if connectionResult}
						<span class="test-result" class:success={connectionResult.success} class:failure={!connectionResult.success}>
							{connectionResult.message}
						</span>
					{/if}
				</div>
			</section>

			<section class="card" id="org-payments">
				<h2>{m('org.section.payments')}</h2>
				<p class="card-hint">
					{m('org.payments.hint')}
				</p>

				<div class="form-grid">
					<label>
						<span>{m('org.payments.provider')}</span>
						<select bind:value={paymentsProvider}>
							<option value="mock">{m('org.payments.providerMock')}</option>
							<option value="modern_treasury">Modern Treasury</option>
						</select>
					</label>
				</div>

				{#if paymentsProvider === 'modern_treasury'}
					<div class="form-grid">
						<label>
							<span>{m('org.payments.orgId')}</span>
							<input type="text" bind:value={paymentsOrgId} placeholder="org_..." />
						</label>
						<label>
							<span>{m('org.payments.apiKey')}</span>
							<input type="password" bind:value={paymentsApiKey} placeholder="••••••••" autocomplete="off" />
						</label>
						<label>
							<span>{m('org.payments.originatingAccount')}</span>
							<input type="text" bind:value={paymentsOriginatingAccount} placeholder={m('org.payments.originatingAccountPlaceholder')} />
						</label>
						<label>
							<span>{m('org.payments.webhookSecret')}</span>
							<input type="password" bind:value={paymentsWebhookSecret} placeholder={m('org.payments.webhookSecretPlaceholder')} autocomplete="off" />
						</label>
						<label class="switch-row">
							<input type="checkbox" bind:checked={paymentsSandbox} />
							<span>{m('org.payments.sandbox')}</span>
						</label>
					</div>
					<p class="card-hint">
						{m('org.payments.webhookHint')}
						<code>{org.created_at ? `${window.location.origin.replace(window.location.host, org.slug + '.' + window.location.host)}/api/payments/webhook/${org.slug}/modern_treasury` : '...'}</code>
					</p>
				{/if}

				<div class="form-grid">
					<label>
						<span>{m('org.payments.cfoThreshold')}</span>
						<input
							type="number"
							min="0"
							step="100"
							placeholder={m('org.payments.cfoThresholdPlaceholder')}
							value={paymentsCfoThreshold ?? ''}
							oninput={(e) => {
								const v = (e.currentTarget as HTMLInputElement).value;
								paymentsCfoThreshold = v ? parseFloat(v) : null;
							}}
						/>
					</label>
				</div>
				<p class="card-hint">
					{m('org.payments.cfoHint')}
				</p>

				<div class="section-footer">
					<button class="btn-save-section" disabled={savingPayments} onclick={savePayments}>
						{savingPayments ? m('org.common.saving') : m('org.payments.save')}
					</button>
					<button class="btn-test" disabled={testingPayments || paymentsProvider === 'mock'} onclick={testPayments}>
						{testingPayments ? m('org.common.testing') : m('org.common.testConnection')}
					</button>
					{#if paymentsTestResult}
						<span class="test-result" class:success={paymentsTestResult.success} class:failure={!paymentsTestResult.success}>
							{paymentsTestResult.message}
						</span>
					{/if}
				</div>
			</section>

			<section class="card">
				<h2>{m('org.section.cards')}</h2>
				<p class="card-hint">{m('org.cards.hint')}</p>

				<div class="form-grid">
					<label>
						<span>{m('org.cards.enabled')}</span>
						<select bind:value={cardsEnabled}>
							<option value={false}>{m('org.cards.disabled')}</option>
							<option value={true}>{m('org.cards.enabledOn')}</option>
						</select>
					</label>
					<label>
						<span>{m('org.cards.program')}</span>
						<select bind:value={cardsProgramType}>
							<option value="platform">{m('org.cards.programPlatform')}</option>
							<option value="byok">{m('org.cards.programByok')}</option>
						</select>
					</label>
					<label>
						<span>{m('org.cards.region')}</span>
						<select bind:value={cardsRegion}>
							{#each CARD_REGIONS as r}
								<option value={r.value}>{r.label}</option>
							{/each}
						</select>
					</label>
					<label>
						<span>{m('org.cards.expiryDays')}</span>
						<input type="number" min="1" max="90" bind:value={cardsExpiryDays} />
					</label>
				</div>

				{#if cardsEnabled && cardsProgramType === 'platform'}
					<p class="card-hint" style="margin-top: 10px;">{m('org.cards.platformHint', { provider: autoProvider === 'lithic' ? 'Lithic' : 'Nium' })}</p>
				{/if}

				{#if cardsEnabled && cardsProgramType === 'byok'}
					<div class="form-grid" style="margin-top: 14px;">
						<label>
							<span>{m('org.cards.provider')}</span>
							<select bind:value={cardsProvider}>
								<option value="">{m('org.cards.providerAuto', { provider: autoProvider === 'lithic' ? 'Lithic' : 'Nium' })}</option>
								<option value="lithic">{m('org.cards.providerLithic')}</option>
								<option value="nium">{m('org.cards.providerNium')}</option>
							</select>
						</label>
					</div>

					{#if effectiveProvider === 'lithic'}
						<div class="form-grid" style="margin-top: 14px;">
							<label>
								<span>{m('org.cards.lithicApiKey')}</span>
								<input type="password" bind:value={cardsApiKey} placeholder="api-key-..." />
							</label>
							<label>
								<span>{m('org.cards.sandboxMode')}</span>
								<select bind:value={cardsSandbox}>
									<option value={true}>{m('org.cards.sandboxTesting')}</option>
									<option value={false}>{m('org.cards.production')}</option>
								</select>
							</label>
						</div>
					{:else if effectiveProvider === 'nium'}
						<div class="form-grid" style="margin-top: 14px;">
							<label>
								<span>{m('org.cards.clientId')}</span>
								<input type="text" bind:value={cardsClientId} />
							</label>
							<label>
								<span>{m('org.cards.clientSecret')}</span>
								<input type="password" bind:value={cardsClientSecret} />
							</label>
							<label>
								<span>{m('org.cards.customerHashId')}</span>
								<input type="text" bind:value={cardsCustomerHashId} />
							</label>
							<label>
								<span>{m('org.cards.walletHashId')}</span>
								<input type="text" bind:value={cardsWalletHashId} />
							</label>
							<label>
								<span>{m('org.cards.sandboxMode')}</span>
								<select bind:value={cardsSandbox}>
									<option value={true}>{m('org.cards.sandboxTesting')}</option>
									<option value={false}>{m('org.cards.production')}</option>
								</select>
							</label>
						</div>
					{/if}
				{/if}

				<div class="section-footer">
					<button class="btn-save-section" disabled={savingCards} onclick={saveCards}>
						{savingCards ? m('org.common.saving') : m('org.cards.save')}
					</button>
				</div>
			</section>

			<section class="card">
				<h2>{m('org.section.security')}</h2>
				<p class="card-hint">
					{m('org.security.hint')}
				</p>

				<label class="switch-row">
					<input type="checkbox" bind:checked={mfaRequired} />
					<span>{m('org.security.requireMfa')}</span>
				</label>

				{#if mfaRequired && !mfaEnforcementActive}
					<p class="mfa-enforcement-warning" role="alert" data-testid="mfa-enforcement-inactive">
						{m('org.security.mfaEnforcementInactive')}
					</p>
				{/if}

				<div class="section-footer">
					<button class="btn-save-section" disabled={savingSecurity} onclick={saveSecurity}>
						{savingSecurity ? m('org.common.saving') : m('org.security.save')}
					</button>
				</div>
			</section>

			{#if fraud}
				<section class="card">
					<h2>{m('org.section.fraud')}</h2>
					<p class="card-hint">
						{m('org.fraud.hint')}
					</p>

					<div class="fraud-grid">
						<label class="switch-row">
							<input type="checkbox" bind:checked={fraud.round_amount_enabled} />
							<span>
								<strong>{m('org.fraud.roundAmount')}</strong>
								<span class="rule-hint">
									{m('org.fraud.roundAmountHint', { min: fraud.round_amount_min })}
								</span>
							</span>
						</label>
						<div class="threshold-row">
							<label>
								<span>{m('org.fraud.minAmount')}</span>
								<input
									type="number"
									min="0"
									step="100"
									bind:value={fraud.round_amount_min}
									disabled={!fraud.round_amount_enabled}
								/>
							</label>
						</div>

						<label class="switch-row">
							<input type="checkbox" bind:checked={fraud.future_date_enabled} />
							<span>
								<strong>{m('org.fraud.futureDate')}</strong>
								<span class="rule-hint">
									{m('org.fraud.futureDateHint')}
								</span>
							</span>
						</label>

						<label class="switch-row">
							<input type="checkbox" bind:checked={fraud.rush_payment_enabled} />
							<span>
								<strong>{m('org.fraud.rushPayment')}</strong>
								<span class="rule-hint">
									{m('org.fraud.rushPaymentHint')}
								</span>
							</span>
						</label>
						<div class="threshold-row">
							<label>
								<span>{m('org.fraud.maxDays')}</span>
								<input
									type="number"
									min="0"
									max="30"
									bind:value={fraud.rush_payment_max_days}
									disabled={!fraud.rush_payment_enabled}
								/>
							</label>
						</div>

						<label class="switch-row">
							<input type="checkbox" bind:checked={fraud.new_vendor_large_enabled} />
							<span>
								<strong>{m('org.fraud.newVendorLarge')}</strong>
								<span class="rule-hint">
									{m('org.fraud.newVendorLargeHint', { amount: fraud.new_vendor_large_amount, days: fraud.new_vendor_max_age_days })}
								</span>
							</span>
						</label>
						<div class="threshold-row">
							<label>
								<span>{m('org.fraud.vendorAge')}</span>
								<input
									type="number"
									min="1"
									bind:value={fraud.new_vendor_max_age_days}
									disabled={!fraud.new_vendor_large_enabled}
								/>
							</label>
							<label>
								<span>{m('org.fraud.largeThreshold')}</span>
								<input
									type="number"
									min="0"
									step="500"
									bind:value={fraud.new_vendor_large_amount}
									disabled={!fraud.new_vendor_large_enabled}
								/>
							</label>
						</div>

						<label class="switch-row">
							<input type="checkbox" bind:checked={fraud.bank_change_enabled} />
							<span>
								<strong>{m('org.fraud.bankChange')}</strong>
								<span class="rule-hint">
									{m('org.fraud.bankChangeHint')}
								</span>
							</span>
						</label>

						<label class="switch-row">
							<input type="checkbox" bind:checked={fraud.personal_email_enabled} />
							<span>
								<strong>{m('org.fraud.personalEmail')}</strong>
								<span class="rule-hint">
									{m('org.fraud.personalEmailHint')}
								</span>
							</span>
						</label>
						<div class="threshold-row">
							<label class="full">
								<span>{m('org.fraud.personalEmailDomains')}</span>
								<textarea
									rows="4"
									bind:value={personalEmailDomainsText}
									disabled={!fraud.personal_email_enabled}
								></textarea>
							</label>
						</div>

						<label class="switch-row">
							<input type="checkbox" bind:checked={fraud.stat_anomaly_enabled} />
							<span>
								<strong>{m('org.fraud.statAnomaly')}</strong>
								<span class="rule-hint">
									{m('org.fraud.statAnomalyHint')}
								</span>
							</span>
						</label>
						<div class="threshold-row">
							<label>
								<span>{m('org.fraud.sigma')}</span>
								<input
									type="number"
									min="0.5"
									step="0.1"
									bind:value={fraud.stat_anomaly_sigma}
									disabled={!fraud.stat_anomaly_enabled}
								/>
							</label>
							<label>
								<span>{m('org.fraud.minPriorInvoices')}</span>
								<input
									type="number"
									min="2"
									bind:value={fraud.stat_anomaly_min_history}
									disabled={!fraud.stat_anomaly_enabled}
								/>
							</label>
						</div>

						<label class="switch-row">
							<input type="checkbox" bind:checked={fraud.llm_anomaly_enabled} />
							<span>
								<strong>{m('org.fraud.llmAnomaly')}</strong>
								<span class="rule-hint">
									{m('org.fraud.llmAnomalyHint')}
								</span>
							</span>
						</label>
					</div>

					<div class="section-footer">
						<button
							type="button"
							class="btn-link"
							onclick={resetFraudToDefaults}
							disabled={savingFraud}
						>
							{m('org.fraud.resetDefaults')}
						</button>
						<button
							class="btn-save-section"
							disabled={savingFraud}
							onclick={saveFraud}
						>
							{savingFraud ? m('org.common.saving') : m('org.fraud.save')}
						</button>
					</div>
				</section>
			{/if}

			<section class="card">
				<h2>{m('org.section.dataSync')}</h2>
				<p class="card-hint">{m('org.dataSync.hint')}</p>

				<div class="sync-grid">
					<div class="sync-item">
						<div class="sync-info">
							<span class="sync-name">{m('org.dataSync.coa')}</span>
							<span class="sync-desc">{m('org.dataSync.coaDesc')}</span>
						</div>
						<button class="btn-outline" disabled={syncingGL} onclick={syncGLAccounts}>
							{syncingGL ? m('org.dataSync.syncing') : m('org.dataSync.syncGl')}
						</button>
						{#if glSyncResult}
							<span class="sync-result">{glSyncResult}</span>
						{/if}
					</div>

					<div class="sync-item">
						<div class="sync-info">
							<span class="sync-name">{m('org.dataSync.pos')}</span>
							<span class="sync-desc">{m('org.dataSync.posDesc')}</span>
						</div>
						<button class="btn-outline" disabled={syncingPOs} onclick={syncPurchaseOrders}>
							{syncingPOs ? m('org.dataSync.syncing') : m('org.dataSync.syncPos')}
						</button>
						{#if poSyncResult}
							<span class="sync-result">{poSyncResult}</span>
						{/if}
					</div>

					<div class="sync-item">
						<div class="sync-info">
							<span class="sync-name">{m('org.dataSync.vendors')}</span>
							<span class="sync-desc">{m('org.dataSync.vendorsDesc')}</span>
						</div>
						<a href="/vendors" class="btn-outline">{m('org.dataSync.manageVendors')}</a>
					</div>
				</div>
			</section>

			<section class="card plan-card">
				<h2>{m('org.section.plan')}</h2>
				<div class="plan-info">
					<span class="plan-badge">{planLabel(org.plan)}</span>
					<span class="plan-slug">{m('org.plan.tenant')} <code>{org.slug}</code></span>
					<span class="plan-date">{m('org.plan.created', { date: formatDate(org.created_at, '—', { month: 'long', day: 'numeric', year: 'numeric' }) })}</span>
				</div>
			</section>
		</div>
	{:else}
		<div class="loading">{m('org.loading')}</div>
	{/if}
</PageHeader>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */
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

	/* First-time-admin wayfinding — links jump to the sections a new tenant
	   configures first. Purely a shortcut; nothing is hidden. */
	.getting-started {
		border-color: var(--accent);
	}

	.gs-links {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 18px;
		margin-top: 4px;
	}

	.gs-links a {
		font-size: 0.85rem;
		color: var(--accent);
		text-decoration: none;
	}

	.gs-links a:hover {
		text-decoration: underline;
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

	/* The field label only — not the .color-field wrapper span (reset below). */
	label > span:first-child {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	/* Branding color picker: native swatch + hex text input side by side. */
	.color-field {
		display: flex;
		align-items: center;
		gap: 8px;
		text-transform: none;
		letter-spacing: normal;
	}

	.color-field input[type='color'] {
		width: 40px;
		height: 36px;
		padding: 2px;
		flex-shrink: 0;
		cursor: pointer;
	}

	.color-field input[type='text'] {
		flex: 1;
	}

	/* Custom domains */
	.domain-list {
		list-style: none;
		margin: 4px 0 16px;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.domain-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 8px 12px;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
	}

	.domain-name {
		font-size: 0.9rem;
		word-break: break-all;
	}

	.residency-placement {
		font-family: var(--font-mono);
		margin: 12px 0 0;
	}

	/* Advisory verdict box. Tinted-badge recipe: each tone takes its own
	   -tint background with the matching -on-tint text (never the base token,
	   which lands under 4.5:1 once composited over the tint). Unknown — the
	   deliberately non-committal state — is the muted default. */
	.residency-alignment {
		margin-top: 14px;
		padding: 10px 12px;
		border-radius: 6px;
		font-size: 0.82rem;
		background: var(--muted-tint);
		color: var(--muted-on-tint);
	}

	.residency-alignment.ok {
		background: var(--success-tint);
		color: var(--success-on-tint);
	}

	.residency-alignment.warn {
		background: var(--warning-tint);
		color: var(--warning-on-tint);
	}

	.residency-alignment strong {
		display: block;
		margin-bottom: 4px;
	}

	.residency-alignment p {
		margin: 0;
	}

	/* Inherits the box's calibrated colour — a muted token here would be
	   judged against the bare surface, not the tint it actually sits on. */
	.residency-advisory {
		margin-top: 6px;
		font-size: 0.78rem;
	}

	.btn-remove-domain {
		flex-shrink: 0;
		padding: 4px 12px;
		font-size: 0.82rem;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: transparent;
		color: var(--text);
		cursor: pointer;
	}

	.btn-remove-domain:hover:not(:disabled),
	.btn-remove-domain.armed {
		/* --danger-strong, not --danger: this armed state is a fill carrying
		   white text, and the old #e5484d fallback was 3.91:1. */
		border-color: var(--danger-strong);
		color: #fff;
		background: var(--danger-strong);
	}

	.btn-remove-domain:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.domain-add {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.domain-add input {
		flex: 1;
	}

	/* Panel-level load failure (custom domains, data residency, chat) — a
	   persistent region rather than a toast, because it explains why the panel
	   is empty. */
	.domain-error,
	.residency-error,
	.chat-error {
		color: var(--danger);
		font-size: 0.88rem;
		margin: 4px 0 12px;
	}

	/* "Enabled for a real provider but no webhook stored" — the adapter fails
	   closed and posts nothing, so this is a live misconfiguration, not an
	   error the user just caused. Tinted-badge recipe: the -tint background
	   with its matching -on-tint text, never the base token. */
	.chat-warning {
		margin: 4px 0 12px;
		padding: 10px 12px;
		border-radius: 6px;
		font-size: 0.82rem;
		background: var(--warning-tint);
		color: var(--warning-on-tint);
	}

	.chat-subhead {
		margin: 20px 0 4px;
		font-size: 0.95rem;
		font-weight: 600;
		color: var(--text);
	}

	.chat-events {
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 12px 14px;
		margin: 14px 0 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.chat-events legend {
		padding: 0 6px;
		font-size: 0.82rem;
		font-weight: 600;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.chat-events .card-hint {
		margin: 0 0 4px;
	}

	.chat-webhook-status {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin: 8px 0 12px;
		flex-wrap: wrap;
	}

	.chat-webhook-set {
		font-size: 0.88rem;
		color: var(--text);
	}

	.domain-empty {
		margin-bottom: 12px;
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
		background: var(--accent-strong);
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
		color: var(--danger);
	}

	.sync-grid {
		display: flex;
		flex-direction: column;
		gap: 12px;
		margin-top: 14px;
	}

	.sync-item {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 10px 12px;
		background: var(--bg);
		border-radius: 6px;
	}

	.sync-info {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.sync-name {
		font-size: 0.88rem;
		font-weight: 500;
		color: var(--text);
	}

	.sync-desc {
		font-size: 0.78rem;
		color: var(--text-muted);
	}

	.sync-result {
		font-size: 0.82rem;
		color: var(--text-muted);
		white-space: nowrap;
	}

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
		text-decoration: none;
		display: inline-block;
		text-align: center;
	}

	.btn-outline:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}

	.btn-outline:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.loading {
		text-align: center;
		padding: 40px;
		color: var(--text-muted);
	}

	label.switch-row {
		flex-direction: row;
		align-items: center;
		gap: 10px;
		font-size: 0.9rem;
		color: var(--text);
		cursor: pointer;
	}

	label.switch-row span {
		font-size: 0.9rem;
		font-weight: 400;
		color: var(--text);
		text-transform: none;
		letter-spacing: normal;
	}

	label.switch-row input[type='checkbox'] {
		width: 16px;
		height: 16px;
		accent-color: var(--accent);
		cursor: pointer;
		flex-shrink: 0;
	}

	.mfa-enforcement-warning {
		color: var(--warning-on-tint);
		font-size: 0.82rem;
		font-weight: 600;
		margin: 8px 0 0;
	}

	/* --- Fraud detection panel --- */

	.fraud-grid {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}

	.fraud-grid label.switch-row {
		align-items: flex-start;
	}

	.fraud-grid .rule-hint {
		display: block;
		margin-top: 2px;
		font-size: 0.8rem;
		color: var(--text-muted);
		font-weight: 400;
	}

	.threshold-row {
		display: grid;
		grid-template-columns: repeat(2, minmax(180px, 1fr));
		gap: 12px;
		margin-left: 26px; /* align under the switch label */
		margin-bottom: 4px;
	}

	.threshold-row label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.threshold-row label.full {
		grid-column: 1 / -1;
	}

	.threshold-row label span {
		font-size: 0.72rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.threshold-row input,
	.threshold-row textarea {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.88rem;
		color: var(--text);
		font-family: inherit;
	}

	.threshold-row textarea {
		resize: vertical;
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.82rem;
	}

	.threshold-row input:focus,
	.threshold-row textarea:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.threshold-row input:disabled,
	.threshold-row textarea:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.btn-link {
		background: none;
		border: none;
		color: var(--text-muted);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
		padding: 0;
		margin-right: auto;
	}

	.btn-link:hover:not(:disabled) {
		color: var(--accent);
	}

	@media (max-width: 600px) {
		.form-grid {
			grid-template-columns: 1fr;
		}
		.threshold-row {
			grid-template-columns: 1fr;
		}
	}
</style>
