import type { Messages } from '../messages';

// French catalogue. Lazy-imported by the runtime (catalogues.ts) so it
// splits into its own chunk. `satisfies Messages` makes a missing/extra
// key a compile error; messages_parity.test.ts re-checks at runtime.
export const messages = {
	// Primary navigation — top-level links
	'nav.dashboard': 'Tableau de bord',
	'nav.invoices': 'Factures',
	'nav.payments': 'Paiements',
	'nav.vendors': 'Fournisseurs',
	'nav.exceptions': 'Exceptions',

	// Primary navigation — group labels
	'nav.group.procurement': 'Approvisionnement',
	'nav.group.billing': 'Facturation',
	'nav.group.insights': 'Analyses',
	'nav.group.settings': 'Paramètres',

	// Primary navigation — group children
	'nav.purchaseOrders': 'Bons de commande',
	'nav.goodsReceipts': 'Réceptions de marchandises',
	'nav.requisitions': 'Demandes d’achat',
	'nav.intake': 'Réception',
	'nav.catalogs': 'Catalogues',
	'nav.budgets': 'Budgets',
	'nav.contracts': 'Contrats',
	'nav.expenses': 'Notes de frais',
	'nav.creditMemos': 'Avoirs',
	'nav.discounts': 'Escomptes',
	'nav.recurring': 'Récurrentes',
	'nav.statements': 'Relevés',
	'nav.positivePay': 'Positive Pay',
	'nav.platformBilling': 'Abonnement',
	'nav.aiAssistant': 'Assistant IA',
	'nav.cashFlow': 'Trésorerie',
	'nav.taxReporting': 'Déclaration 1099',
	'nav.organization': 'Organisation',
	'nav.users': 'Utilisateurs',
	'nav.roles': 'Rôles',
	'nav.auditTrail': 'Piste d’audit',
	'nav.workflows': 'Flux de travail',

	// App shell / sidebar
	'shell.appName': 'Account Payables',
	'shell.skipToMain': 'Aller au contenu principal',
	'shell.primaryNav': 'Navigation principale',
	'shell.sectionNav': 'Sections : {group}',
	'shell.profileMenu': 'Menu profil et compte',
	'shell.profile': 'Profil',
	'shell.profileAndSecurity': 'Profil et sécurité',
	'shell.logOut': 'Se déconnecter',
	'shell.expandSidebar': 'Déplier la barre latérale',
	'shell.collapseSidebar': 'Replier la barre latérale',
	'shell.collapse': 'Replier',

	// Common buttons / states
	'common.save': 'Enregistrer',
	'common.saving': 'Enregistrement…',
	'common.cancel': 'Annuler',
	'common.loading': 'Chargement…',

	// Profile → Language picker
	'profile.language.heading': 'Langue',
	'profile.language.hint':
		'Choisissez la langue utilisée dans toute l’application. Votre choix est enregistré sur cet appareil.',
	'profile.language.label': 'Langue d’affichage',

	// Common shared across list/detail surfaces
	'common.all': 'Toutes',
	'common.search': 'Rechercher',
	'common.clear': 'Effacer',
	'common.apply': 'Appliquer',

	// Dashboard
	'dashboard.title': 'Tableau de bord',
	'dashboard.kpi.invoices': 'Factures',
	'dashboard.kpi.totalAmount': 'Montant total',
	'dashboard.kpi.paid': 'Payées',
	'dashboard.kpi.pending': 'En attente',
	'dashboard.kpi.touchlessRate': 'Taux d’automatisation',
	'dashboard.kpi.exceptions': 'Exceptions',
	'dashboard.kpi.staleApprovals': 'Approbations en retard',
	'dashboard.kpi.rebatesEarned': 'Remises obtenues',
	'dashboard.chart.pipeline': 'Pipeline des factures',
	'dashboard.chart.topVendors': 'Principaux fournisseurs par dépense',
	'dashboard.chart.aging': 'Ancienneté des factures',
	'dashboard.chart.upcoming': 'À venir et en retard',
	'dashboard.chart.monthlyVolume': 'Volume mensuel',
	'dashboard.empty.vendors': 'Aucune donnée de facture pour le moment.',
	'dashboard.empty.aging': 'Aucune facture ouverte avec date d’échéance.',
	'dashboard.empty.upcoming': 'Aucun paiement prévu cette semaine.',
	'dashboard.aging.current': 'À jour',
	'dashboard.aging.days30': '1 à 30 jours',
	'dashboard.aging.days60': '31 à 60 jours',
	'dashboard.aging.days90': '61 à 90 jours',
	'dashboard.aging.days90plus': 'plus de 90 jours',
	'dashboard.overdue': 'En retard',

	// Invoices list
	'invoices.title': 'Factures',
	'invoices.action.bulkRecode': 'Recoder les comptes',
	'invoices.action.upload': '+ Importer des factures',
	'invoices.action.uploading': 'Importation…',
	'invoices.action.uploadingProgress': 'Importation de {done} sur {total}…',
	'invoices.search.placeholder': 'Rechercher des factures…',
	'invoices.search.aria': 'Rechercher des factures',
	'invoices.search.advanced': 'Recherche avancée',
	'invoices.bulk.selected': '{n, plural, one {# sélectionnée} other {# sélectionnées}}',
	'invoices.bulk.delete': 'Supprimer',
	'invoices.bulk.confirmDelete': 'Confirmer la suppression',
	'invoices.bulk.changeStatus': 'Changer le statut',
	'invoices.bulk.cannotDelete': 'Impossible de supprimer des factures dans un statut géré par le système',
	'invoices.bulk.noTransitions': 'Aucune transition de statut commune pour les factures sélectionnées',
	'invoices.bulk.newStatusAria': 'Nouveau statut pour les factures sélectionnées',
	'invoices.col.invoiceNumber': 'N° de facture',
	'invoices.col.vendor': 'Fournisseur',
	'invoices.col.description': 'Description',
	'invoices.col.poNumber': 'N° de commande',
	'invoices.col.amount': 'Montant',
	'invoices.col.dueDate': 'Échéance',
	'invoices.col.status': 'Statut',
	'invoices.selectAllAria': 'Sélectionner toutes les factures',
	'invoices.empty': 'Aucune facture ne correspond à vos filtres.',
	'invoices.row.delete': 'Supprimer',
	'invoices.row.confirm': 'Confirmer',
	'invoices.loadMore': 'Charger plus ({shown} sur {total})',
	'invoices.showingAll': '{total, plural, one {Affichage de la # facture} other {Affichage des # factures}}',
} satisfies Messages;
