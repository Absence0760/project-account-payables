// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for French (`fr`).
class AppLocalizationsFr extends AppLocalizations {
  AppLocalizationsFr([String locale = 'fr']) : super(locale);

  @override
  String get navDashboard => 'Tableau de bord';

  @override
  String get navInvoices => 'Factures';

  @override
  String get navContracts => 'Contrats';

  @override
  String get navApprovals => 'Approbations';

  @override
  String get navExceptions => 'Exceptions';

  @override
  String get navVendors => 'Fournisseurs';

  @override
  String get navPay => 'Payer';

  @override
  String get navPayments => 'Paiements';

  @override
  String get navSettings => 'Paramètres';

  @override
  String get shellAppName => 'Account Payables';

  @override
  String get commonSave => 'Enregistrer';

  @override
  String get commonSaving => 'Enregistrement…';

  @override
  String get commonCancel => 'Annuler';

  @override
  String get commonLoading => 'Chargement…';

  @override
  String get commonRetry => 'Réessayer';

  @override
  String get commonAll => 'Toutes';

  @override
  String get commonSearch => 'Rechercher';

  @override
  String get commonClear => 'Effacer';

  @override
  String get commonApply => 'Appliquer';

  @override
  String get settingsTitle => 'Paramètres';

  @override
  String get settingsTenant => 'Locataire';

  @override
  String get settingsTenantNotSet => 'Non défini';

  @override
  String get settingsApiServer => 'Serveur API';

  @override
  String get settingsBiometricUnlock => 'Déverrouillage biométrique';

  @override
  String get settingsBiometricHint =>
      'Utiliser l’empreinte ou le visage pour déverrouiller';

  @override
  String get settingsSignOut => 'Se déconnecter';

  @override
  String get settingsLanguage => 'Langue';

  @override
  String get settingsLanguageHint =>
      'Choisissez la langue utilisée dans toute l’application. Votre choix est enregistré sur cet appareil.';

  @override
  String get settingsLanguageSystem => 'Paramètre du système';

  @override
  String get dashboardTitle => 'Tableau de bord';

  @override
  String get dashboardTotalInvoices => 'Total des factures';

  @override
  String get dashboardUpcoming => 'À venir';

  @override
  String get dashboardForReview => 'À examiner';

  @override
  String get dashboardApproved => 'Approuvées';

  @override
  String get dashboardAging => 'Ancienneté des factures';

  @override
  String get dashboardTopVendors => 'Principaux fournisseurs';

  @override
  String get dashboardAgingCurrent => 'À jour';

  @override
  String get dashboardAgingDays30 => '30 jours';

  @override
  String get dashboardAgingDays60 => '60 jours';

  @override
  String get dashboardAgingDays90plus => '90 et +';

  @override
  String get dashboardCachedBanner => 'Données en cache — serveur injoignable';

  @override
  String dashboardErrorPrefix(String error) {
    return 'Erreur : $error';
  }

  @override
  String dashboardInvoiceCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count factures',
      one: '$count facture',
    );
    return '$_temp0';
  }

  @override
  String get invoicesTitle => 'Factures';

  @override
  String get invoicesSearchHint => 'Rechercher des factures…';

  @override
  String get invoicesSearchAria => 'Rechercher des factures';

  @override
  String get invoicesAdvancedSearch => 'Recherche avancée';

  @override
  String get invoicesAdvancedSearchActive =>
      'Recherche avancée, filtres actifs';

  @override
  String get invoicesCaptureInvoice => 'Capturer une facture';

  @override
  String get invoicesCaptureInvoiceLabel => 'Capturer une facture';

  @override
  String get invoicesEmpty => 'Aucune facture trouvée';

  @override
  String get invoicesFilterAll => 'Toutes';

  @override
  String get invoicesFilterNew => 'Nouvelles';

  @override
  String get invoicesFilterPending => 'En attente';

  @override
  String get invoicesFilterReview => 'Examen';

  @override
  String get invoicesFilterApproved => 'Approuvées';

  @override
  String get invoicesFilterRejected => 'Rejetées';

  @override
  String get invoicesFilterPaid => 'Payées';

  @override
  String get invoicesColInvoiceNumber => 'N° de facture';

  @override
  String get invoicesColVendor => 'Fournisseur';

  @override
  String get invoicesColAmount => 'Montant';

  @override
  String get invoicesColDueDate => 'Échéance';

  @override
  String get invoicesColStatus => 'Statut';

  @override
  String get notificationsTitle => 'Notifications';

  @override
  String get notificationsMarkAllRead => 'Tout marquer comme lu';

  @override
  String get notificationsMarkAllReadLabel =>
      'Marquer toutes les notifications comme lues';

  @override
  String get notificationsFilterUnread => 'Non lues';

  @override
  String get notificationsAllMarkedRead =>
      'Toutes les notifications marquées comme lues';

  @override
  String get notificationsCouldNotMarkAll =>
      'Impossible de tout marquer comme lu';

  @override
  String get notificationsEmptyUnread => 'Aucune notification non lue';

  @override
  String get notificationsEmpty => 'Aucune notification';

  @override
  String get notificationsCaughtUp => 'Vous êtes à jour';

  @override
  String get notificationsNothingYet => 'Rien pour le moment';

  @override
  String get notificationsLoadError =>
      'Impossible de charger les notifications';

  @override
  String get vendorsTitle => 'Fournisseurs';

  @override
  String get vendorsSyncErp => 'Synchroniser depuis l\'ERP';

  @override
  String get vendorsSyncErpLabel =>
      'Synchroniser les fournisseurs depuis l\'ERP';

  @override
  String get vendorsSearchHint => 'Rechercher des fournisseurs…';

  @override
  String get vendorsFilterUnverified => 'Non vérifiés';

  @override
  String get vendorsFilterActive => 'Actifs';

  @override
  String get vendorsFilterInactive => 'Inactifs';

  @override
  String get vendorsFilterRejected => 'Rejetés';

  @override
  String get vendorsEmpty => 'Aucun fournisseur trouvé';

  @override
  String get vendorsLoadError => 'Impossible de charger les fournisseurs';

  @override
  String get vendorActionVerify => 'Vérifier';

  @override
  String get vendorActionReject => 'Rejeter';

  @override
  String get vendorUnverifiedLabel => 'Fournisseur non vérifié';

  @override
  String get vendorVerifyHint => 'Rendre éligible au paiement';

  @override
  String get vendorRejectHint => 'Marquer comme non valide / en double';

  @override
  String get vendorVerified => 'Fournisseur vérifié';

  @override
  String get vendorRejected => 'Fournisseur rejeté';

  @override
  String get vendorActionFailed => 'Échec de l\'action';

  @override
  String vendorSyncFailed(String error) {
    return 'Échec de la synchronisation ERP : $error';
  }

  @override
  String get exceptionsTitle => 'Exceptions';

  @override
  String get exceptionsFilterOpen => 'Ouvertes';

  @override
  String get exceptionsFilterEscalated => 'Escaladées';

  @override
  String get exceptionsFilterResolved => 'Résolues';

  @override
  String get exceptionsFilterDismissed => 'Ignorées';

  @override
  String get exceptionsEmpty => 'Aucune exception';

  @override
  String get exceptionsQueueClear =>
      'La file d\'attente des exceptions est vide';

  @override
  String get exceptionActionResolve => 'Résoudre';

  @override
  String get exceptionActionEscalate => 'Escalader';

  @override
  String get exceptionActionDismiss => 'Ignorer';

  @override
  String get exceptionResolved => 'Exception résolue';

  @override
  String get exceptionEscalated => 'Exception escaladée';

  @override
  String get exceptionDismissed => 'Exception ignorée';

  @override
  String get exceptionActionFailed => 'Échec de l\'action';

  @override
  String get paymentsTitle => 'Paiements';

  @override
  String get paymentsEmpty => 'Aucun paiement';

  @override
  String paymentsErrorPrefix(String error) {
    return 'Erreur : $error';
  }

  @override
  String get paymentStatusPending => 'En attente';

  @override
  String get paymentStatusProcessing => 'En cours';

  @override
  String get paymentStatusCompleted => 'Terminé';

  @override
  String get paymentStatusFailed => 'Échoué';

  @override
  String get paymentStatusCancelled => 'Annulé';
}
