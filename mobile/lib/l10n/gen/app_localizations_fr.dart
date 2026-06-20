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
  String get commonClose => 'Fermer';

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

  @override
  String get approvalsTitle => 'Approbations en attente';

  @override
  String get approvalsAllCaughtUp => 'Tout est à jour !';

  @override
  String get approvalsNoneWaiting => 'Aucune facture en attente d\'approbation';

  @override
  String approvalsPendingCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count factures en attente',
      one: '$count facture en attente',
    );
    return '$_temp0';
  }

  @override
  String get approvalActionApprove => 'Approuver';

  @override
  String get approvalActionReject => 'Rejeter';

  @override
  String get approvalApproved => 'Facture approuvée';

  @override
  String get captureTitle => 'Capturer une facture';

  @override
  String get captureChange => 'Modifier';

  @override
  String get captureUpload => 'Téléverser';

  @override
  String get captureUploading => 'Téléversement…';

  @override
  String get captureEmptyPrompt =>
      'Prenez une photo, choisissez dans la galerie ou sélectionnez un fichier';

  @override
  String get captureCamera => 'Appareil photo';

  @override
  String get captureGallery => 'Galerie';

  @override
  String get captureChooseFile => 'Choisir un fichier';

  @override
  String get captureSupportedFormats => 'Prend en charge PDF, PNG, JPG et TIFF';

  @override
  String get captureUploadSuccess => 'Facture téléversée avec succès';

  @override
  String captureUploadFailedStatus(int status, String message) {
    return 'Échec du téléversement ($status) : $message';
  }

  @override
  String captureUploadFailed(String error) {
    return 'Échec du téléversement : $error';
  }

  @override
  String captureSelectedDocument(String name) {
    return 'Document sélectionné : $name';
  }

  @override
  String get capturePdfReady => 'Document PDF prêt à être téléversé';

  @override
  String get advSearchTitle => 'Recherche avancée';

  @override
  String get advSearchClose => 'Fermer la recherche avancée';

  @override
  String get advSearchVendor => 'Fournisseur';

  @override
  String get advSearchPoNumber => 'N° de commande';

  @override
  String get advSearchMinAmount => 'Montant min.';

  @override
  String get advSearchMaxAmount => 'Montant max.';

  @override
  String get advSearchDueFrom => 'Échéance à partir du';

  @override
  String get advSearchDueTo => 'Échéance jusqu\'au';

  @override
  String get advSearchAny => 'Toute';

  @override
  String get advSearchInvalidAmount =>
      'Saisissez un montant valide (par ex. 1000)';

  @override
  String get advSearchMinMaxError =>
      'Le minimum ne doit pas dépasser le maximum';

  @override
  String advSearchClearField(String label) {
    return 'Effacer $label';
  }

  @override
  String advSearchDateFieldHint(String label, String value) {
    return '$label, actuellement $value. Appuyez deux fois pour modifier.';
  }

  @override
  String get invoiceDetailTitle => 'Détail de la facture';

  @override
  String get invoiceDetailEdit => 'Modifier';

  @override
  String get invoiceDetailEditLabel => 'Modifier la facture';

  @override
  String get invoiceDetailRetry => 'Réessayer';

  @override
  String invoiceDetailErrorPrefix(String error) {
    return 'Erreur : $error';
  }

  @override
  String get invoiceDetailNoChanges => 'Aucune modification à enregistrer';

  @override
  String get invoiceDetailUpdated => 'Facture mise à jour';

  @override
  String get invoiceDetailUpdateFailed =>
      'Impossible d\'enregistrer les modifications — veuillez réessayer';

  @override
  String get invoiceDetailApproved => 'Facture approuvée';

  @override
  String get invoiceDetailApproveFailed =>
      'Impossible d\'approuver la facture — veuillez réessayer';

  @override
  String get invoiceDetailRejected => 'Facture rejetée';

  @override
  String get invoiceDetailRejectFailed =>
      'Impossible de rejeter la facture — veuillez réessayer';

  @override
  String get invoiceDetailRejectTitle => 'Rejeter la facture';

  @override
  String get invoiceDetailRejectReason => 'Motif';

  @override
  String get invoiceDetailReject => 'Rejeter';

  @override
  String get invoiceDetailApprove => 'Approuver';

  @override
  String get invoiceDetailUnknownVendor => 'Fournisseur inconnu';

  @override
  String get invoiceDetailFieldInvoiceNumber => 'N° de facture';

  @override
  String get invoiceDetailFieldPoNumber => 'N° de commande';

  @override
  String get invoiceDetailFieldCurrency => 'Devise';

  @override
  String get invoiceDetailFieldInvoiceDate => 'Date de facture';

  @override
  String get invoiceDetailFieldDueDate => 'Date d\'échéance';

  @override
  String get invoiceDetailFieldDescription => 'Description';

  @override
  String get invoiceDetailFieldGlAccount => 'Compte comptable';

  @override
  String get invoiceDetailFieldCreated => 'Créée';

  @override
  String get invoiceDetailActivity => 'Activité';

  @override
  String get invoiceDetailActivityError => 'Impossible de charger l\'activité';

  @override
  String get invoiceDetailFilePdfLabel =>
      'PDF de la facture. Appuyez deux fois pour afficher en plein écran.';

  @override
  String get invoiceDetailFileLabel =>
      'Fichier de la facture. Appuyez deux fois pour afficher en plein écran.';

  @override
  String get invoiceDetailTapToViewPdf => 'Appuyez pour afficher le PDF';

  @override
  String get invoiceDetailTapToViewFile => 'Appuyez pour afficher le fichier';

  @override
  String get invoiceEditTitle => 'Modifier la facture';

  @override
  String get invoiceEditClose => 'Fermer le formulaire de modification';

  @override
  String get invoiceEditVendor => 'Fournisseur';

  @override
  String get invoiceEditInvoiceNumber => 'N° de facture';

  @override
  String get invoiceEditAmount => 'Montant';

  @override
  String get invoiceEditPoNumber => 'N° de commande';

  @override
  String get invoiceEditGlAccount => 'Compte comptable';

  @override
  String get invoiceEditDescription => 'Description';

  @override
  String get invoiceEditDueDate => 'Date d\'échéance';

  @override
  String get invoiceEditNotSet => 'Non défini';

  @override
  String get invoiceEditInvalidAmount =>
      'Saisissez un montant valide (par ex. 1234,56)';

  @override
  String get invoiceEditClearDueDate => 'Effacer la date d\'échéance';

  @override
  String invoiceEditDueDateHint(String value) {
    return 'Date d\'échéance, actuellement $value. Appuyez deux fois pour modifier.';
  }

  @override
  String get warningsSectionTitle => 'Avertissements et alertes de fraude';

  @override
  String get warningsPoMatchTitle => 'Rapprochement de commande';

  @override
  String get warningsSeverityError => 'Erreur';

  @override
  String get warningsSeverityWarning => 'Avertissement';

  @override
  String get warningsSeverityInfo => 'Info';

  @override
  String get warningsPoLabel => 'Commande';

  @override
  String warningsMatchLabel(String type) {
    return 'Rapprochement $type';
  }

  @override
  String warningsVarianceLabel(String value) {
    return '$value % d\'écart';
  }

  @override
  String get erpStatusTitle => 'Statut ERP';

  @override
  String get erpStatusReference => 'Référence ERP';

  @override
  String get erpStatusDocumentId => 'ID du document';

  @override
  String get erpStatusError => 'Erreur';

  @override
  String get erpStatusLastUpdate => 'Dernière mise à jour';

  @override
  String get erpStatusStatus => 'Statut';

  @override
  String get fileViewerPdfTitle => 'PDF de la facture';

  @override
  String get fileViewerImageTitle => 'Image de la facture';

  @override
  String get fileViewerPdfError => 'Impossible de charger le PDF';

  @override
  String get fileViewerImageError => 'Impossible de charger l\'image';

  @override
  String get fileViewerRetry => 'Réessayer';

  @override
  String get timelineNoActivity => 'Aucune activité pour l\'instant';

  @override
  String get payTitle => 'Payer';

  @override
  String get payTabQueue => 'File d\'attente';

  @override
  String get payTabRuns => 'Lots';

  @override
  String get paySummaryTotalPaid => 'Total payé';

  @override
  String get paySummaryPending => 'En attente';

  @override
  String get paySummaryInQueue => 'En file';

  @override
  String get paySummaryCardRebates => 'Remises sur carte';

  @override
  String paySummaryPaymentsSubtitle(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count paiements',
      one: '$count paiement',
    );
    return '$_temp0';
  }

  @override
  String get payQueueEmpty => 'Aucune facture en attente de paiement';

  @override
  String get payQueueError => 'Impossible de charger la file de paiement';

  @override
  String get payQueueRetry => 'Réessayer';

  @override
  String payQueueDue(String date) {
    return 'Échéance $date';
  }

  @override
  String get payQueueNoDueDate => 'Aucune date d\'échéance';

  @override
  String payQueueDiscount(String amount) {
    return 'remise $amount';
  }

  @override
  String get payQueueOverdue => 'en retard';

  @override
  String get payQueueSelected => 'sélectionnée';

  @override
  String payMethodLabel(String invoiceNumber) {
    return 'Mode de paiement pour $invoiceNumber';
  }

  @override
  String get payMethodAch => 'ACH';

  @override
  String get payMethodWire => 'Virement';

  @override
  String get payMethodCheck => 'Chèque';

  @override
  String get payMethodVirtualCard => 'Carte virtuelle';

  @override
  String paySelectedCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count factures sélectionnées',
      one: '$count facture sélectionnée',
    );
    return '$_temp0';
  }

  @override
  String get payClear => 'Effacer';

  @override
  String get payCreateRun => 'Créer un lot';

  @override
  String payCreateRunFailed(String error) {
    return 'Échec de la création du lot : $error';
  }

  @override
  String get payRunsEmpty => 'Aucun lot de paiement';

  @override
  String payRunSubtitle(int count, String date) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count paiements',
      one: '$count paiement',
    );
    return '$_temp0 • $date';
  }

  @override
  String get payRunCfoRequiredSuffix =>
      ' • Approbation du directeur financier requise';

  @override
  String payRunAnnounce(String amount, String status, String subtitle) {
    return 'Lot $amount, $status, $subtitle';
  }

  @override
  String get payRunActions => 'Actions du lot';

  @override
  String get payRunActionExecute => 'Exécuter';

  @override
  String get payRunActionCancel => 'Annuler';

  @override
  String get payRunCfoBlocked =>
      'Ce lot nécessite l\'approbation du directeur financier avant de pouvoir être exécuté.';

  @override
  String get payRunExecuteTitle => 'Exécuter le lot de paiement ?';

  @override
  String payRunExecuteBody(String amount) {
    return 'Cette action envoie $amount via le processeur de paiement configuré.';
  }

  @override
  String payRunExecuteFailed(String error) {
    return 'Échec de l\'exécution : $error';
  }

  @override
  String payRunCancelFailed(String error) {
    return 'Échec de l\'annulation : $error';
  }

  @override
  String get payRunStatusDraft => 'Brouillon';

  @override
  String get payRunStatusCompleted => 'Terminé';

  @override
  String get payRunStatusSubmitted => 'Soumis';

  @override
  String get payRunStatusPartial => 'Partiel';

  @override
  String get payRunStatusFailed => 'Échoué';

  @override
  String get payRunStatusCancelled => 'Annulé';

  @override
  String get payConfirmCancel => 'Annuler';

  @override
  String get payConfirmExecute => 'Exécuter';

  @override
  String get loginAppName => 'Better AP';

  @override
  String get loginTagline => 'La comptabilité fournisseurs, simplifiée';

  @override
  String get loginTenant => 'Locataire';

  @override
  String get loginEmail => 'E-mail';

  @override
  String get loginPassword => 'Mot de passe';

  @override
  String get loginShowPassword => 'Afficher le mot de passe';

  @override
  String get loginHidePassword => 'Masquer le mot de passe';

  @override
  String get loginRequired => 'Obligatoire';

  @override
  String get loginSignIn => 'Se connecter';

  @override
  String get mfaTitle => 'Authentification à deux facteurs';

  @override
  String get mfaHeading => 'Vérifiez votre identité';

  @override
  String get mfaPromptEmail =>
      'Saisissez le code à 6 chiffres que nous vous avons envoyé par e-mail.';

  @override
  String get mfaPromptTotp =>
      'Saisissez le code à 6 chiffres de votre application d\'authentification.';

  @override
  String get mfaEnforcedNotice =>
      'Votre organisation exige une authentification à deux facteurs. Vérifiez maintenant avec un code par e-mail, puis terminez la configuration d\'une application d\'authentification dans l\'application web.';

  @override
  String get mfaCode => 'Code';

  @override
  String get mfaCodeRequired => 'Obligatoire';

  @override
  String get mfaCodeTooShort => 'Saisissez au moins 6 chiffres';

  @override
  String get mfaVerify => 'Vérifier';

  @override
  String get mfaSending => 'Envoi…';

  @override
  String get mfaResendEmailCode => 'Renvoyer le code par e-mail';

  @override
  String get mfaSendEmailCode => 'Envoyer le code par e-mail';

  @override
  String get mfaUseEmailInstead => 'Utiliser plutôt un code par e-mail';

  @override
  String get mfaUseAuthenticatorInstead =>
      'Utiliser plutôt l\'application d\'authentification';

  @override
  String get mfaEmailedAnnounce =>
      'Un code de connexion vous a été envoyé par e-mail.';

  @override
  String get adminUsersTitle => 'Gestion des utilisateurs';

  @override
  String get adminUsersSearchHint => 'Rechercher par nom ou e-mail';

  @override
  String get adminUsersEmpty => 'Aucun utilisateur trouvé';

  @override
  String get adminUsersLoadError => 'Impossible de charger les utilisateurs';

  @override
  String get adminUsersEditRoles => 'Modifier les rôles';

  @override
  String get adminUsersNoRoles => 'Aucun rôle';

  @override
  String get adminUsersDeactivate => 'Désactiver l\'utilisateur';

  @override
  String get adminUsersActivate => 'Activer l\'utilisateur';

  @override
  String get adminUsersCannotDeactivateSelf =>
      'Vous ne pouvez pas désactiver votre propre compte';

  @override
  String get adminUsersDeactivateHint =>
      'Les déconnecte et bloque la connexion';

  @override
  String get adminUsersActivateHint => 'Restaure l\'accès à la connexion';

  @override
  String get adminUsersRoleActive => 'actif';

  @override
  String get adminUsersRoleInactive => 'inactif';

  @override
  String get adminUsersInactiveBadge => 'Inactif';

  @override
  String adminUsersRolesUpdated(String name) {
    return 'Rôles mis à jour pour $name';
  }

  @override
  String adminUsersRolesUpdateFailed(String error) {
    return 'Échec de la mise à jour des rôles : $error';
  }

  @override
  String adminUsersActivated(String name) {
    return '$name activé';
  }

  @override
  String adminUsersDeactivated(String name) {
    return '$name désactivé';
  }

  @override
  String adminUsersUpdateFailed(String name, String error) {
    return 'Échec de la mise à jour de $name : $error';
  }

  @override
  String get orgSettingsTitle => 'Paramètres de l\'organisation';

  @override
  String get orgSettingsNoSettings => 'Aucun paramètre';

  @override
  String get orgSettingsLoadError => 'Impossible de charger les paramètres';

  @override
  String get orgSettingsSectionCompany => 'Entreprise';

  @override
  String get orgSettingsSectionInvoiceDefaults =>
      'Valeurs par défaut des factures';

  @override
  String get orgSettingsName => 'Nom de l\'organisation';

  @override
  String get orgSettingsAddress => 'Adresse';

  @override
  String get orgSettingsPhone => 'Téléphone';

  @override
  String get orgSettingsWebsite => 'Site web';

  @override
  String get orgSettingsTaxId => 'Numéro fiscal';

  @override
  String get orgSettingsCurrency => 'Devise par défaut';

  @override
  String get orgSettingsPaymentTerms => 'Conditions de paiement';

  @override
  String get orgSettingsNumberPrefix => 'Préfixe de numéro de facture';

  @override
  String get orgSettingsGlAccount => 'Compte général par défaut';

  @override
  String get orgSettingsCostCenter => 'Centre de coûts par défaut';

  @override
  String get orgSettingsSave => 'Enregistrer les modifications';

  @override
  String get orgSettingsSaving => 'Enregistrement…';

  @override
  String orgSettingsFieldRequired(String label) {
    return '$label est obligatoire';
  }

  @override
  String get orgSettingsSaved => 'Paramètres de l\'organisation enregistrés';

  @override
  String orgSettingsSaveFailed(String error) {
    return 'Échec de l\'enregistrement : $error';
  }

  @override
  String get workflowsTitle => 'Flux de travail';

  @override
  String get workflowsEmpty => 'Aucun flux de travail trouvé';

  @override
  String get workflowsLoadError => 'Impossible de charger les flux de travail';

  @override
  String get workflowsStatusActive => 'Actif';

  @override
  String get workflowsStatusInactive => 'Inactif';

  @override
  String get workflowsDefault => 'Par défaut';

  @override
  String workflowsStepCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count étapes',
      one: '$count étape',
    );
    return '$_temp0';
  }

  @override
  String get workflowDetailFallbackTitle => 'Flux de travail';

  @override
  String get workflowDetailLoadError =>
      'Impossible de charger le flux de travail';

  @override
  String get workflowDetailNoSteps => 'Ce flux de travail n\'a aucune étape.';

  @override
  String get workflowDetailDefaultWorkflow => 'Flux de travail par défaut';

  @override
  String workflowDetailStepNumber(int number) {
    return 'Étape $number';
  }

  @override
  String get workflowDetailStepEnabled => 'Activé';

  @override
  String get workflowDetailStepDisabled => 'Désactivé';

  @override
  String workflowDetailApproverCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count approbateurs',
      one: '$count approbateur',
    );
    return '$_temp0';
  }

  @override
  String workflowDetailDelaySummary(String hours) {
    return 'Délai $hours h';
  }

  @override
  String workflowDetailConditionSummary(String field) {
    return 'Sur $field';
  }
}
