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
} satisfies Messages;
