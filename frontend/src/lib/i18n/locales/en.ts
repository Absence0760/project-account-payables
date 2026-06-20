// Source-of-truth message catalogue. Every other locale must define
// exactly these keys (enforced by `satisfies Messages` on each locale
// module and by messages_parity.test.ts). Keys are flat + dotted, grouped
// by surface; `{name}`-style placeholders are filled by m()'s params arg.
//
// English is statically bundled (it is the fallback for any missing key
// and the prerender default); other locales are lazy-imported by the
// runtime in store.svelte.ts so a single-locale visitor only downloads
// their own strings.
//
// FIRST SLICE: this catalogue covers the app shell + primary nav + the
// profile locale picker only. The rest of the app stays hardcoded English
// until later extraction slices — that's the intended incremental path.

export const en = {
	// Primary navigation — top-level links ($lib/nav.ts)
	'nav.dashboard': 'Dashboard',
	'nav.invoices': 'Invoices',
	'nav.payments': 'Payments',
	'nav.vendors': 'Vendors',
	'nav.exceptions': 'Exceptions',

	// Primary navigation — group labels ($lib/nav.ts)
	'nav.group.procurement': 'Procurement',
	'nav.group.billing': 'Billing',
	'nav.group.insights': 'Insights',
	'nav.group.settings': 'Settings',

	// Primary navigation — group children ($lib/nav.ts)
	'nav.purchaseOrders': 'Purchase Orders',
	'nav.goodsReceipts': 'Goods Receipts',
	'nav.requisitions': 'Requisitions',
	'nav.intake': 'Intake',
	'nav.catalogs': 'Catalogs',
	'nav.budgets': 'Budgets',
	'nav.contracts': 'Contracts',
	'nav.expenses': 'Expenses',
	'nav.creditMemos': 'Credit Memos',
	'nav.discounts': 'Discounts',
	'nav.recurring': 'Recurring',
	'nav.statements': 'Statements',
	'nav.positivePay': 'Positive Pay',
	'nav.aiAssistant': 'AI Assistant',
	'nav.cashFlow': 'Cash Flow',
	'nav.taxReporting': '1099 Reporting',
	'nav.organization': 'Organization',
	'nav.users': 'Users',
	'nav.roles': 'Roles',
	'nav.auditTrail': 'Audit Trail',
	'nav.workflows': 'Workflows',

	// App shell / sidebar (Sidebar.svelte, +layout.svelte)
	'shell.appName': 'Account Payables',
	'shell.skipToMain': 'Skip to main content',
	'shell.primaryNav': 'Primary',
	'shell.sectionNav': '{group} sections',
	'shell.profileMenu': 'Profile and account menu',
	'shell.profile': 'Profile',
	'shell.profileAndSecurity': 'Profile & Security',
	'shell.logOut': 'Log Out',
	'shell.expandSidebar': 'Expand sidebar',
	'shell.collapseSidebar': 'Collapse sidebar',
	'shell.collapse': 'Collapse',

	// Common buttons / states (reused across surfaces)
	'common.save': 'Save',
	'common.saving': 'Saving…',
	'common.cancel': 'Cancel',
	'common.loading': 'Loading…',

	// Profile → Language picker (profile/+page.svelte)
	'profile.language.heading': 'Language',
	'profile.language.hint':
		'Choose the language used across the app. Your choice is saved on this device.',
	'profile.language.label': 'Display language',
} satisfies Record<string, string>;
