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

	// Common shared across list/detail surfaces
	'common.all': 'All',
	'common.search': 'Search',
	'common.clear': 'Clear',
	'common.apply': 'Apply',

	// Dashboard (routes/+page.svelte)
	'dashboard.title': 'Dashboard',
	'dashboard.kpi.invoices': 'Invoices',
	'dashboard.kpi.totalAmount': 'Total Amount',
	'dashboard.kpi.paid': 'Paid',
	'dashboard.kpi.pending': 'Pending',
	'dashboard.kpi.touchlessRate': 'Touchless Rate',
	'dashboard.kpi.exceptions': 'Exceptions',
	'dashboard.kpi.staleApprovals': 'Stale Approvals',
	'dashboard.kpi.rebatesEarned': 'Rebates Earned',
	'dashboard.chart.pipeline': 'Invoice Pipeline',
	'dashboard.chart.topVendors': 'Top Vendors by Spend',
	'dashboard.chart.aging': 'Invoice Aging',
	'dashboard.chart.upcoming': 'Upcoming & Overdue',
	'dashboard.chart.monthlyVolume': 'Monthly Volume',
	'dashboard.empty.vendors': 'No invoice data yet.',
	'dashboard.empty.aging': 'No open invoices with due dates.',
	'dashboard.empty.upcoming': 'No upcoming payments this week.',
	'dashboard.aging.current': 'Current',
	'dashboard.aging.days30': '1-30 days',
	'dashboard.aging.days60': '31-60 days',
	'dashboard.aging.days90': '61-90 days',
	'dashboard.aging.days90plus': '90+ days',
	'dashboard.overdue': 'Overdue',

	// Invoices list (routes/invoices/+page.svelte)
	'invoices.title': 'Invoices',
	'invoices.action.bulkRecode': 'Bulk Re-code GL',
	'invoices.action.upload': '+ Upload Invoices',
	'invoices.action.uploading': 'Uploading...',
	'invoices.action.uploadingProgress': 'Uploading {done} of {total}...',
	'invoices.search.placeholder': 'Search invoices...',
	'invoices.search.aria': 'Search invoices',
	'invoices.search.advanced': 'Advanced search',
	'invoices.bulk.selected': '{n, plural, one {# selected} other {# selected}}',
	'invoices.bulk.delete': 'Delete',
	'invoices.bulk.confirmDelete': 'Confirm Delete',
	'invoices.bulk.changeStatus': 'Change Status',
	'invoices.bulk.cannotDelete': 'Cannot delete invoices in system-managed statuses',
	'invoices.bulk.noTransitions': 'No common status transitions for the selected invoices',
	'invoices.bulk.newStatusAria': 'New status for selected invoices',
	'invoices.col.invoiceNumber': 'Invoice #',
	'invoices.col.vendor': 'Vendor',
	'invoices.col.description': 'Description',
	'invoices.col.poNumber': 'PO #',
	'invoices.col.amount': 'Amount',
	'invoices.col.dueDate': 'Due Date',
	'invoices.col.status': 'Status',
	'invoices.selectAllAria': 'Select all invoices',
	'invoices.empty': 'No invoices match your filters.',
	'invoices.row.delete': 'Delete',
	'invoices.row.confirm': 'Confirm',
	'invoices.loadMore': 'Load more ({shown} of {total})',
	'invoices.showingAll': '{total, plural, one {Showing all # invoice} other {Showing all # invoices}}',
} satisfies Record<string, string>;
