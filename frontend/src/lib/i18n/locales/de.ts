import type { Messages } from '../messages';

// German catalogue. Lazy-imported by the runtime (catalogues.ts) so it
// splits into its own chunk. `satisfies Messages` makes a missing/extra
// key a compile error; messages_parity.test.ts re-checks at runtime.
export const messages = {
	// Primary navigation — top-level links
	'nav.dashboard': 'Übersicht',
	'nav.invoices': 'Rechnungen',
	'nav.payments': 'Zahlungen',
	'nav.vendors': 'Lieferanten',
	'nav.exceptions': 'Ausnahmen',

	// Primary navigation — group labels
	'nav.group.procurement': 'Beschaffung',
	'nav.group.billing': 'Abrechnung',
	'nav.group.insights': 'Auswertungen',
	'nav.group.settings': 'Einstellungen',

	// Primary navigation — group children
	'nav.purchaseOrders': 'Bestellungen',
	'nav.goodsReceipts': 'Wareneingänge',
	'nav.requisitions': 'Bedarfsanforderungen',
	'nav.intake': 'Eingang',
	'nav.catalogs': 'Kataloge',
	'nav.budgets': 'Budgets',
	'nav.contracts': 'Verträge',
	'nav.expenses': 'Spesen',
	'nav.creditMemos': 'Gutschriften',
	'nav.discounts': 'Skonti',
	'nav.recurring': 'Wiederkehrend',
	'nav.statements': 'Kontoauszüge',
	'nav.positivePay': 'Positive Pay',
	'nav.platformBilling': 'Abonnement',
	'nav.aiAssistant': 'KI-Assistent',
	'nav.cashFlow': 'Liquidität',
	'nav.taxReporting': '1099-Meldung',
	'nav.organization': 'Organisation',
	'nav.users': 'Benutzer',
	'nav.roles': 'Rollen',
	'nav.auditTrail': 'Prüfprotokoll',
	'nav.workflows': 'Workflows',

	// App shell / sidebar
	'shell.appName': 'Account Payables',
	'shell.skipToMain': 'Zum Hauptinhalt springen',
	'shell.primaryNav': 'Hauptnavigation',
	'shell.sectionNav': 'Abschnitte: {group}',
	'shell.profileMenu': 'Profil- und Kontomenü',
	'shell.profile': 'Profil',
	'shell.profileAndSecurity': 'Profil & Sicherheit',
	'shell.logOut': 'Abmelden',
	'shell.expandSidebar': 'Seitenleiste ausklappen',
	'shell.collapseSidebar': 'Seitenleiste einklappen',
	'shell.collapse': 'Einklappen',

	// Common buttons / states
	'common.save': 'Speichern',
	'common.saving': 'Wird gespeichert …',
	'common.cancel': 'Abbrechen',
	'common.loading': 'Wird geladen …',

	// Profile → Language picker
	'profile.language.heading': 'Sprache',
	'profile.language.hint':
		'Wählen Sie die in der App verwendete Sprache. Ihre Auswahl wird auf diesem Gerät gespeichert.',
	'profile.language.label': 'Anzeigesprache',

	// Common shared across list/detail surfaces
	'common.all': 'Alle',
	'common.search': 'Suchen',
	'common.clear': 'Löschen',
	'common.apply': 'Anwenden',

	// Dashboard
	'dashboard.title': 'Übersicht',
	'dashboard.kpi.invoices': 'Rechnungen',
	'dashboard.kpi.totalAmount': 'Gesamtbetrag',
	'dashboard.kpi.paid': 'Bezahlt',
	'dashboard.kpi.pending': 'Ausstehend',
	'dashboard.kpi.touchlessRate': 'Automatisierungsquote',
	'dashboard.kpi.exceptions': 'Ausnahmen',
	'dashboard.kpi.staleApprovals': 'Überfällige Freigaben',
	'dashboard.kpi.rebatesEarned': 'Erzielte Rückvergütungen',
	'dashboard.chart.pipeline': 'Rechnungs-Pipeline',
	'dashboard.chart.topVendors': 'Top-Lieferanten nach Ausgaben',
	'dashboard.chart.aging': 'Rechnungsalter',
	'dashboard.chart.upcoming': 'Anstehend & Überfällig',
	'dashboard.chart.monthlyVolume': 'Monatsvolumen',
	'dashboard.empty.vendors': 'Noch keine Rechnungsdaten.',
	'dashboard.empty.aging': 'Keine offenen Rechnungen mit Fälligkeitsdatum.',
	'dashboard.empty.upcoming': 'Diese Woche keine anstehenden Zahlungen.',
	'dashboard.aging.current': 'Aktuell',
	'dashboard.aging.days30': '1–30 Tage',
	'dashboard.aging.days60': '31–60 Tage',
	'dashboard.aging.days90': '61–90 Tage',
	'dashboard.aging.days90plus': 'über 90 Tage',
	'dashboard.overdue': 'Überfällig',

	// Invoices list
	'invoices.title': 'Rechnungen',
	'invoices.action.bulkRecode': 'Sachkonten neu zuordnen',
	'invoices.action.upload': '+ Rechnungen hochladen',
	'invoices.action.uploading': 'Wird hochgeladen …',
	'invoices.action.uploadingProgress': '{done} von {total} werden hochgeladen …',
	'invoices.search.placeholder': 'Rechnungen suchen …',
	'invoices.search.aria': 'Rechnungen suchen',
	'invoices.search.advanced': 'Erweiterte Suche',
	'invoices.bulk.selected': '{n, plural, one {# ausgewählt} other {# ausgewählt}}',
	'invoices.bulk.delete': 'Löschen',
	'invoices.bulk.confirmDelete': 'Löschen bestätigen',
	'invoices.bulk.changeStatus': 'Status ändern',
	'invoices.bulk.cannotDelete': 'Rechnungen in systemverwalteten Status können nicht gelöscht werden',
	'invoices.bulk.noTransitions': 'Keine gemeinsamen Statusübergänge für die ausgewählten Rechnungen',
	'invoices.bulk.newStatusAria': 'Neuer Status für ausgewählte Rechnungen',
	'invoices.col.invoiceNumber': 'Rechnungs-Nr.',
	'invoices.col.vendor': 'Lieferant',
	'invoices.col.description': 'Beschreibung',
	'invoices.col.poNumber': 'Bestell-Nr.',
	'invoices.col.amount': 'Betrag',
	'invoices.col.dueDate': 'Fälligkeitsdatum',
	'invoices.col.status': 'Status',
	'invoices.selectAllAria': 'Alle Rechnungen auswählen',
	'invoices.empty': 'Keine Rechnungen entsprechen Ihren Filtern.',
	'invoices.row.delete': 'Löschen',
	'invoices.row.confirm': 'Bestätigen',
	'invoices.loadMore': 'Mehr laden ({shown} von {total})',
	'invoices.showingAll': '{total, plural, one {Alle # Rechnung werden angezeigt} other {Alle # Rechnungen werden angezeigt}}',
} satisfies Messages;
