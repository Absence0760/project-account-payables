// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for German (`de`).
class AppLocalizationsDe extends AppLocalizations {
  AppLocalizationsDe([String locale = 'de']) : super(locale);

  @override
  String get navDashboard => 'Übersicht';

  @override
  String get navInvoices => 'Rechnungen';

  @override
  String get navContracts => 'Verträge';

  @override
  String get navApprovals => 'Freigaben';

  @override
  String get navExceptions => 'Ausnahmen';

  @override
  String get navVendors => 'Lieferanten';

  @override
  String get navPay => 'Bezahlen';

  @override
  String get navPayments => 'Zahlungen';

  @override
  String get navSettings => 'Einstellungen';

  @override
  String get shellAppName => 'FeohLedger';

  @override
  String get commonSave => 'Speichern';

  @override
  String get commonSaving => 'Wird gespeichert …';

  @override
  String get commonCancel => 'Abbrechen';

  @override
  String get commonLoading => 'Wird geladen …';

  @override
  String get commonRetry => 'Erneut versuchen';

  @override
  String get commonAll => 'Alle';

  @override
  String get commonSearch => 'Suchen';

  @override
  String get commonClear => 'Löschen';

  @override
  String get commonApply => 'Anwenden';

  @override
  String get commonClose => 'Schließen';

  @override
  String get settingsTitle => 'Einstellungen';

  @override
  String get settingsTenant => 'Mandant';

  @override
  String get settingsTenantNotSet => 'Nicht festgelegt';

  @override
  String get settingsApiServer => 'API-Server';

  @override
  String get settingsBiometricUnlock => 'Biometrisches Entsperren';

  @override
  String get settingsBiometricHint =>
      'Fingerabdruck oder Gesicht zum Entsperren verwenden';

  @override
  String get settingsSignOut => 'Abmelden';

  @override
  String get settingsLanguage => 'Sprache';

  @override
  String get settingsLanguageHint =>
      'Wählen Sie die in der App verwendete Sprache. Ihre Auswahl wird auf diesem Gerät gespeichert.';

  @override
  String get settingsLanguageSystem => 'Systemstandard';

  @override
  String get dashboardTitle => 'Übersicht';

  @override
  String get dashboardTotalInvoices => 'Rechnungen gesamt';

  @override
  String get dashboardUpcoming => 'Anstehend';

  @override
  String get dashboardForReview => 'Zur Prüfung';

  @override
  String get dashboardApproved => 'Freigegeben';

  @override
  String get dashboardAging => 'Rechnungsalter';

  @override
  String get dashboardTopVendors => 'Top-Lieferanten';

  @override
  String get dashboardAgingCurrent => 'Aktuell';

  @override
  String get dashboardAgingDays30 => '30 Tage';

  @override
  String get dashboardAgingDays60 => '60 Tage';

  @override
  String get dashboardAgingDays90plus => 'über 90';

  @override
  String get dashboardCachedBanner =>
      'Zwischengespeicherte Daten — Server nicht erreichbar';

  @override
  String dashboardErrorPrefix(String error) {
    return 'Fehler: $error';
  }

  @override
  String dashboardInvoiceCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Rechnungen',
      one: '$count Rechnung',
    );
    return '$_temp0';
  }

  @override
  String get invoicesTitle => 'Rechnungen';

  @override
  String get invoicesSearchHint => 'Rechnungen suchen …';

  @override
  String get invoicesSearchAria => 'Rechnungen suchen';

  @override
  String get invoicesAdvancedSearch => 'Erweiterte Suche';

  @override
  String get invoicesAdvancedSearchActive => 'Erweiterte Suche, Filter aktiv';

  @override
  String get invoicesCaptureInvoice => 'Rechnung erfassen';

  @override
  String get invoicesCaptureInvoiceLabel => 'Rechnung erfassen';

  @override
  String get invoicesEmpty => 'Keine Rechnungen gefunden';

  @override
  String get invoicesFilterAll => 'Alle';

  @override
  String get invoicesFilterNew => 'Neu';

  @override
  String get invoicesFilterPending => 'Ausstehend';

  @override
  String get invoicesFilterReview => 'Prüfung';

  @override
  String get invoicesFilterApproved => 'Freigegeben';

  @override
  String get invoicesFilterRejected => 'Abgelehnt';

  @override
  String get invoicesFilterPaid => 'Bezahlt';

  @override
  String get invoicesColInvoiceNumber => 'Rechnungs-Nr.';

  @override
  String get invoicesColVendor => 'Lieferant';

  @override
  String get invoicesColAmount => 'Betrag';

  @override
  String get invoicesColDueDate => 'Fälligkeitsdatum';

  @override
  String get invoicesColStatus => 'Status';

  @override
  String get notificationsTitle => 'Benachrichtigungen';

  @override
  String get notificationsMarkAllRead => 'Alle als gelesen markieren';

  @override
  String get notificationsMarkAllReadLabel =>
      'Alle Benachrichtigungen als gelesen markieren';

  @override
  String get notificationsFilterUnread => 'Ungelesen';

  @override
  String get notificationsAllMarkedRead =>
      'Alle Benachrichtigungen als gelesen markiert';

  @override
  String get notificationsCouldNotMarkAll =>
      'Konnte nicht alle als gelesen markieren';

  @override
  String get notificationsEmptyUnread => 'Keine ungelesenen Benachrichtigungen';

  @override
  String get notificationsEmpty => 'Keine Benachrichtigungen';

  @override
  String get notificationsCaughtUp => 'Sie sind auf dem neuesten Stand';

  @override
  String get notificationsNothingYet => 'Hier ist noch nichts';

  @override
  String get notificationsLoadError =>
      'Benachrichtigungen konnten nicht geladen werden';

  @override
  String get vendorsTitle => 'Lieferanten';

  @override
  String get vendorsSyncErp => 'Aus ERP synchronisieren';

  @override
  String get vendorsSyncErpLabel => 'Lieferanten aus ERP synchronisieren';

  @override
  String get vendorsSearchHint => 'Lieferanten suchen …';

  @override
  String get vendorsFilterUnverified => 'Ungeprüft';

  @override
  String get vendorsFilterActive => 'Aktiv';

  @override
  String get vendorsFilterInactive => 'Inaktiv';

  @override
  String get vendorsFilterRejected => 'Abgelehnt';

  @override
  String get vendorsEmpty => 'Keine Lieferanten gefunden';

  @override
  String get vendorsLoadError => 'Lieferanten konnten nicht geladen werden';

  @override
  String get vendorActionVerify => 'Bestätigen';

  @override
  String get vendorActionReject => 'Ablehnen';

  @override
  String get vendorUnverifiedLabel => 'Ungeprüfter Lieferant';

  @override
  String get vendorVerifyHint => 'Für Zahlung freigeben';

  @override
  String get vendorRejectHint => 'Als ungültig / Dublette markieren';

  @override
  String get vendorVerified => 'Lieferant bestätigt';

  @override
  String get vendorRejected => 'Lieferant abgelehnt';

  @override
  String get vendorActionFailed => 'Aktion fehlgeschlagen';

  @override
  String vendorSyncFailed(String error) {
    return 'ERP-Synchronisierung fehlgeschlagen: $error';
  }

  @override
  String get exceptionsTitle => 'Ausnahmen';

  @override
  String get exceptionsFilterOpen => 'Offen';

  @override
  String get exceptionsFilterEscalated => 'Eskaliert';

  @override
  String get exceptionsFilterResolved => 'Gelöst';

  @override
  String get exceptionsFilterDismissed => 'Verworfen';

  @override
  String get exceptionsEmpty => 'Keine Ausnahmen';

  @override
  String get exceptionsQueueClear => 'Die Ausnahmewarteschlange ist leer';

  @override
  String get exceptionActionResolve => 'Lösen';

  @override
  String get exceptionActionEscalate => 'Eskalieren';

  @override
  String get exceptionActionDismiss => 'Verwerfen';

  @override
  String get exceptionResolved => 'Ausnahme gelöst';

  @override
  String get exceptionEscalated => 'Ausnahme eskaliert';

  @override
  String get exceptionDismissed => 'Ausnahme verworfen';

  @override
  String get exceptionActionFailed => 'Aktion fehlgeschlagen';

  @override
  String get paymentsTitle => 'Zahlungen';

  @override
  String get paymentsEmpty => 'Keine Zahlungen';

  @override
  String paymentsErrorPrefix(String error) {
    return 'Fehler: $error';
  }

  @override
  String get paymentStatusPending => 'Ausstehend';

  @override
  String get paymentStatusProcessing => 'In Bearbeitung';

  @override
  String get paymentStatusCompleted => 'Abgeschlossen';

  @override
  String get paymentStatusFailed => 'Fehlgeschlagen';

  @override
  String get paymentStatusCancelled => 'Storniert';

  @override
  String get approvalsTitle => 'Ausstehende Freigaben';

  @override
  String get approvalsAllCaughtUp => 'Alles erledigt!';

  @override
  String get approvalsNoneWaiting => 'Keine Rechnungen warten auf Freigabe';

  @override
  String get approvalsLoadError =>
      'Ausstehende Freigaben konnten nicht geladen werden';

  @override
  String approvalsPendingCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Rechnungen ausstehend',
      one: '$count Rechnung ausstehend',
    );
    return '$_temp0';
  }

  @override
  String get approvalActionApprove => 'Freigeben';

  @override
  String get approvalActionReject => 'Ablehnen';

  @override
  String get approvalApproved => 'Rechnung freigegeben';

  @override
  String get captureTitle => 'Rechnung erfassen';

  @override
  String get captureChange => 'Ändern';

  @override
  String get captureUpload => 'Hochladen';

  @override
  String get captureUploading => 'Wird hochgeladen …';

  @override
  String get captureEmptyPrompt =>
      'Foto aufnehmen, aus der Galerie wählen oder eine Datei auswählen';

  @override
  String get captureCamera => 'Kamera';

  @override
  String get captureGallery => 'Galerie';

  @override
  String get captureChooseFile => 'Datei auswählen';

  @override
  String get captureSupportedFormats => 'Unterstützt PDF, PNG, JPG und TIFF';

  @override
  String get captureUploadSuccess => 'Rechnung erfolgreich hochgeladen';

  @override
  String captureUploadFailedStatus(int status, String message) {
    return 'Hochladen fehlgeschlagen ($status): $message';
  }

  @override
  String captureUploadFailed(String error) {
    return 'Hochladen fehlgeschlagen: $error';
  }

  @override
  String captureSelectedDocument(String name) {
    return 'Ausgewähltes Dokument: $name';
  }

  @override
  String get capturePdfReady => 'PDF-Dokument bereit zum Hochladen';

  @override
  String get advSearchTitle => 'Erweiterte Suche';

  @override
  String get advSearchClose => 'Erweiterte Suche schließen';

  @override
  String get advSearchVendor => 'Lieferant';

  @override
  String get advSearchPoNumber => 'Bestellnummer';

  @override
  String get advSearchMinAmount => 'Mindestbetrag';

  @override
  String get advSearchMaxAmount => 'Höchstbetrag';

  @override
  String get advSearchDueFrom => 'Fällig ab';

  @override
  String get advSearchDueTo => 'Fällig bis';

  @override
  String get advSearchAny => 'Beliebig';

  @override
  String get advSearchInvalidAmount => 'Gültigen Betrag eingeben (z. B. 1000)';

  @override
  String get advSearchMinMaxError =>
      'Minimum darf das Maximum nicht überschreiten';

  @override
  String advSearchClearField(String label) {
    return '$label löschen';
  }

  @override
  String advSearchDateFieldHint(String label, String value) {
    return '$label, aktuell $value. Zum Ändern doppeltippen.';
  }

  @override
  String get invoiceDetailTitle => 'Rechnungsdetails';

  @override
  String get invoiceDetailEdit => 'Bearbeiten';

  @override
  String get invoiceDetailEditLabel => 'Rechnung bearbeiten';

  @override
  String get invoiceDetailRetry => 'Erneut versuchen';

  @override
  String invoiceDetailErrorPrefix(String error) {
    return 'Fehler: $error';
  }

  @override
  String get invoiceDetailNoChanges => 'Keine Änderungen zu speichern';

  @override
  String get invoiceDetailUpdated => 'Rechnung aktualisiert';

  @override
  String get invoiceDetailUpdateFailed =>
      'Änderungen konnten nicht gespeichert werden — bitte erneut versuchen';

  @override
  String get invoiceDetailApproved => 'Rechnung freigegeben';

  @override
  String get invoiceDetailApproveFailed =>
      'Rechnung konnte nicht freigegeben werden — bitte erneut versuchen';

  @override
  String get invoiceDetailRejected => 'Rechnung abgelehnt';

  @override
  String get invoiceDetailRejectFailed =>
      'Rechnung konnte nicht abgelehnt werden — bitte erneut versuchen';

  @override
  String get invoiceDetailRejectTitle => 'Rechnung ablehnen';

  @override
  String get invoiceDetailRejectReason => 'Grund';

  @override
  String get invoiceDetailReject => 'Ablehnen';

  @override
  String get invoiceDetailApprove => 'Freigeben';

  @override
  String get invoiceDetailUnknownVendor => 'Unbekannter Lieferant';

  @override
  String get invoiceDetailFieldInvoiceNumber => 'Rechnungs-Nr.';

  @override
  String get invoiceDetailFieldPoNumber => 'Bestellnummer';

  @override
  String get invoiceDetailFieldCurrency => 'Währung';

  @override
  String get invoiceDetailFieldInvoiceDate => 'Rechnungsdatum';

  @override
  String get invoiceDetailFieldDueDate => 'Fälligkeitsdatum';

  @override
  String get invoiceDetailFieldDescription => 'Beschreibung';

  @override
  String get invoiceDetailFieldGlAccount => 'Sachkonto';

  @override
  String get invoiceDetailFieldCreated => 'Erstellt';

  @override
  String get invoiceDetailActivity => 'Aktivität';

  @override
  String get invoiceDetailActivityError =>
      'Aktivität konnte nicht geladen werden';

  @override
  String get invoiceDetailFilePdfLabel =>
      'Rechnungs-PDF. Zum Anzeigen im Vollbild doppeltippen.';

  @override
  String get invoiceDetailFileLabel =>
      'Rechnungsdatei. Zum Anzeigen im Vollbild doppeltippen.';

  @override
  String get invoiceDetailTapToViewPdf => 'Zum Anzeigen des PDFs tippen';

  @override
  String get invoiceDetailTapToViewFile => 'Zum Anzeigen der Datei tippen';

  @override
  String get invoiceEditTitle => 'Rechnung bearbeiten';

  @override
  String get invoiceEditClose => 'Bearbeitungsformular schließen';

  @override
  String get invoiceEditVendor => 'Lieferant';

  @override
  String get invoiceEditInvoiceNumber => 'Rechnungs-Nr.';

  @override
  String get invoiceEditAmount => 'Betrag';

  @override
  String get invoiceEditPoNumber => 'Bestellnummer';

  @override
  String get invoiceEditGlAccount => 'Sachkonto';

  @override
  String get invoiceEditDescription => 'Beschreibung';

  @override
  String get invoiceEditDueDate => 'Fälligkeitsdatum';

  @override
  String get invoiceEditNotSet => 'Nicht festgelegt';

  @override
  String get invoiceEditInvalidAmount =>
      'Gültigen Betrag eingeben (z. B. 1234,56)';

  @override
  String get invoiceEditClearDueDate => 'Fälligkeitsdatum löschen';

  @override
  String get invoiceEditLockedNotice =>
      'Genehmigt — Zahlungsempfänger und Betrag sind gesperrt. Zum Ändern die Rechnung ablehnen, korrigieren und erneut genehmigen.';

  @override
  String get invoiceEditLockedHelper => 'Nach der Genehmigung gesperrt';

  @override
  String invoiceEditDueDateHint(String value) {
    return 'Fälligkeitsdatum, aktuell $value. Zum Ändern doppeltippen.';
  }

  @override
  String get warningsSectionTitle => 'Warnungen & Betrugshinweise';

  @override
  String get warningsPoMatchTitle => 'Bestellabgleich';

  @override
  String get warningsSeverityError => 'Fehler';

  @override
  String get warningsSeverityWarning => 'Warnung';

  @override
  String get warningsSeverityInfo => 'Info';

  @override
  String get warningsPoLabel => 'Bestellung';

  @override
  String warningsMatchLabel(String type) {
    return '$type-Abgleich';
  }

  @override
  String warningsVarianceLabel(String value) {
    return '$value % Abweichung';
  }

  @override
  String get erpStatusTitle => 'ERP-Status';

  @override
  String get erpStatusReference => 'ERP-Referenz';

  @override
  String get erpStatusDocumentId => 'Dokument-ID';

  @override
  String get erpStatusError => 'Fehler';

  @override
  String get erpStatusLastUpdate => 'Letzte Aktualisierung';

  @override
  String get erpStatusStatus => 'Status';

  @override
  String get fileViewerPdfTitle => 'Rechnungs-PDF';

  @override
  String get fileViewerImageTitle => 'Rechnungsbild';

  @override
  String get fileViewerPdfError => 'PDF konnte nicht geladen werden';

  @override
  String get fileViewerImageError => 'Bild konnte nicht geladen werden';

  @override
  String get fileViewerRetry => 'Erneut versuchen';

  @override
  String get timelineNoActivity => 'Noch keine Aktivität';

  @override
  String get payTitle => 'Bezahlen';

  @override
  String get payTabQueue => 'Warteschlange';

  @override
  String get payTabRuns => 'Läufe';

  @override
  String get paySummaryTotalPaid => 'Gesamt bezahlt';

  @override
  String get paySummaryPending => 'Ausstehend';

  @override
  String get paySummaryInQueue => 'In Warteschlange';

  @override
  String get paySummaryCardRebates => 'Karten-Rückvergütungen';

  @override
  String paySummaryPaymentsSubtitle(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Zahlungen',
      one: '$count Zahlung',
    );
    return '$_temp0';
  }

  @override
  String get payQueueEmpty => 'Keine Rechnungen zur Zahlung';

  @override
  String get payQueueError =>
      'Zahlungswarteschlange konnte nicht geladen werden';

  @override
  String get payQueueRetry => 'Erneut versuchen';

  @override
  String payQueueDue(String date) {
    return 'Fällig $date';
  }

  @override
  String get payQueueNoDueDate => 'Kein Fälligkeitsdatum';

  @override
  String payQueueDiscount(String amount) {
    return 'Rabatt $amount';
  }

  @override
  String get payQueueOverdue => 'überfällig';

  @override
  String get payQueueSelected => 'ausgewählt';

  @override
  String payMethodLabel(String invoiceNumber) {
    return 'Zahlungsmethode für $invoiceNumber';
  }

  @override
  String get payMethodAch => 'ACH';

  @override
  String get payMethodWire => 'Überweisung';

  @override
  String get payMethodCheck => 'Scheck';

  @override
  String get payMethodVirtualCard => 'Virtuelle Karte';

  @override
  String paySelectedCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Rechnungen ausgewählt',
      one: '$count Rechnung ausgewählt',
    );
    return '$_temp0';
  }

  @override
  String get payClear => 'Löschen';

  @override
  String get payCreateRun => 'Lauf erstellen';

  @override
  String payCreateRunFailed(String error) {
    return 'Lauf konnte nicht erstellt werden: $error';
  }

  @override
  String get payRunsEmpty => 'Keine Zahlungsläufe';

  @override
  String payRunSubtitle(int count, String date) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Zahlungen',
      one: '$count Zahlung',
    );
    return '$_temp0 • $date';
  }

  @override
  String get payRunCfoRequiredSuffix => ' • CFO-Freigabe erforderlich';

  @override
  String payRunAnnounce(String amount, String status, String subtitle) {
    return 'Lauf $amount, $status, $subtitle';
  }

  @override
  String get payRunActions => 'Lauf-Aktionen';

  @override
  String get payRunActionExecute => 'Ausführen';

  @override
  String get payRunActionCancel => 'Stornieren';

  @override
  String get payRunActionApprove => 'Als CFO freigeben';

  @override
  String get payRunApproveTitle => 'Zahlungslauf freigeben?';

  @override
  String payRunApproveBody(String date, int count, String amount) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Zahlungen',
      one: '$count Zahlung',
    );
    return 'Freigabe des am $date erstellten Laufs — $_temp0 über insgesamt $amount. Dies genehmigt die Ausführung; es wird kein Geld bewegt.';
  }

  @override
  String get payRunApproveConfirm => 'Freigeben';

  @override
  String payRunApproveFailed(String error) {
    return 'Freigabe fehlgeschlagen: $error';
  }

  @override
  String get payRunCfoBlocked =>
      'Dieser Lauf benötigt eine CFO-Freigabe, bevor er ausgeführt werden kann.';

  @override
  String get payRunExecuteTitle => 'Zahlungslauf ausführen?';

  @override
  String payRunExecuteBody(String amount) {
    return 'Dies sendet $amount über den konfigurierten Zahlungsdienstleister.';
  }

  @override
  String payRunExecuteFailed(String error) {
    return 'Ausführen fehlgeschlagen: $error';
  }

  @override
  String payRunCancelFailed(String error) {
    return 'Stornieren fehlgeschlagen: $error';
  }

  @override
  String get payRunStatusDraft => 'Entwurf';

  @override
  String get payRunStatusCompleted => 'Abgeschlossen';

  @override
  String get payRunStatusSubmitted => 'Eingereicht';

  @override
  String get payRunStatusPartial => 'Teilweise';

  @override
  String get payRunStatusFailed => 'Fehlgeschlagen';

  @override
  String get payRunStatusCancelled => 'Storniert';

  @override
  String get payConfirmCancel => 'Abbrechen';

  @override
  String get payConfirmExecute => 'Ausführen';

  @override
  String get loginAppName => 'FeohLedger';

  @override
  String get loginTagline => 'Kreditorenbuchhaltung, ganz einfach';

  @override
  String get loginTenant => 'Mandant';

  @override
  String get loginEmail => 'E-Mail';

  @override
  String get loginPassword => 'Passwort';

  @override
  String get loginShowPassword => 'Passwort anzeigen';

  @override
  String get loginHidePassword => 'Passwort verbergen';

  @override
  String get loginRequired => 'Erforderlich';

  @override
  String get loginSignIn => 'Anmelden';

  @override
  String get mfaTitle => 'Zwei-Faktor-Authentifizierung';

  @override
  String get mfaHeading => 'Bestätigen Sie Ihre Identität';

  @override
  String get mfaPromptEmail =>
      'Geben Sie den 6-stelligen Code ein, den wir Ihnen per E-Mail gesendet haben.';

  @override
  String get mfaPromptTotp =>
      'Geben Sie den 6-stelligen Code aus Ihrer Authenticator-App ein.';

  @override
  String get mfaEnforcedNotice =>
      'Ihre Organisation erfordert eine Zwei-Faktor-Authentifizierung. Bestätigen Sie jetzt mit einem E-Mail-Code und richten Sie anschließend in der Web-App eine Authenticator-App ein.';

  @override
  String get mfaCode => 'Code';

  @override
  String get mfaCodeRequired => 'Erforderlich';

  @override
  String get mfaCodeTooShort => 'Mindestens 6 Ziffern eingeben';

  @override
  String get mfaVerify => 'Bestätigen';

  @override
  String get mfaSending => 'Wird gesendet…';

  @override
  String get mfaResendEmailCode => 'E-Mail-Code erneut senden';

  @override
  String get mfaSendEmailCode => 'E-Mail-Code senden';

  @override
  String get mfaUseEmailInstead => 'Stattdessen einen E-Mail-Code verwenden';

  @override
  String get mfaUseAuthenticatorInstead =>
      'Stattdessen die Authenticator-App verwenden';

  @override
  String get mfaEmailedAnnounce =>
      'Ein Anmeldecode wurde Ihnen per E-Mail gesendet.';

  @override
  String get adminUsersTitle => 'Benutzerverwaltung';

  @override
  String get adminUsersSearchHint => 'Nach Name oder E-Mail suchen';

  @override
  String get adminUsersEmpty => 'Keine Benutzer gefunden';

  @override
  String get adminUsersLoadError => 'Benutzer konnten nicht geladen werden';

  @override
  String get adminUsersEditRoles => 'Rollen bearbeiten';

  @override
  String get adminUsersNoRoles => 'Keine Rollen';

  @override
  String get adminUsersDeactivate => 'Benutzer deaktivieren';

  @override
  String get adminUsersActivate => 'Benutzer aktivieren';

  @override
  String get adminUsersCannotDeactivateSelf =>
      'Sie können Ihr eigenes Konto nicht deaktivieren';

  @override
  String get adminUsersDeactivateHint =>
      'Meldet sie ab und blockiert die Anmeldung';

  @override
  String get adminUsersActivateHint => 'Stellt den Anmeldezugang wieder her';

  @override
  String get adminUsersRoleActive => 'aktiv';

  @override
  String get adminUsersRoleInactive => 'inaktiv';

  @override
  String get adminUsersInactiveBadge => 'Inaktiv';

  @override
  String adminUsersRolesUpdated(String name) {
    return 'Rollen für $name aktualisiert';
  }

  @override
  String adminUsersRolesUpdateFailed(String error) {
    return 'Rollen konnten nicht aktualisiert werden: $error';
  }

  @override
  String adminUsersActivated(String name) {
    return '$name aktiviert';
  }

  @override
  String adminUsersDeactivated(String name) {
    return '$name deaktiviert';
  }

  @override
  String adminUsersUpdateFailed(String name, String error) {
    return '$name konnte nicht aktualisiert werden: $error';
  }

  @override
  String get adminUsersCreateUser => 'Benutzer erstellen';

  @override
  String get adminUsersCreateTitle => 'Neuer Benutzer';

  @override
  String get adminUsersFieldFullName => 'Vollständiger Name';

  @override
  String get adminUsersFieldEmail => 'E-Mail';

  @override
  String get adminUsersFieldRoles => 'Rollen';

  @override
  String get adminUsersValidationNameRequired =>
      'Vollständiger Name ist erforderlich';

  @override
  String get adminUsersValidationEmailRequired => 'E-Mail ist erforderlich';

  @override
  String get adminUsersValidationEmailInvalid =>
      'Geben Sie eine gültige E-Mail-Adresse ein';

  @override
  String get adminUsersCreateSubmit => 'Erstellen';

  @override
  String get adminUsersCreating => 'Wird erstellt…';

  @override
  String adminUsersCreated(String name) {
    return '$name erstellt';
  }

  @override
  String adminUsersCreateFailed(String error) {
    return 'Benutzer konnte nicht erstellt werden: $error';
  }

  @override
  String get adminUsersTempPasswordTitle => 'Benutzer erstellt';

  @override
  String adminUsersTempPasswordBody(String name) {
    return 'Teilen Sie dieses Einmalpasswort mit $name. Bei der ersten Anmeldung wird es geändert. Es wird nicht erneut angezeigt.';
  }

  @override
  String get adminUsersDelete => 'Benutzer löschen';

  @override
  String get adminUsersDeleteHint => 'Entfernt dieses Konto dauerhaft';

  @override
  String get adminUsersCannotDeleteSelf =>
      'Sie können Ihr eigenes Konto nicht löschen';

  @override
  String adminUsersDeleteConfirmTitle(String name) {
    return '$name löschen?';
  }

  @override
  String adminUsersDeleteConfirmBody(String name, String email) {
    return 'Dadurch wird $name ($email) dauerhaft entfernt. Dies kann nicht rückgängig gemacht werden.';
  }

  @override
  String adminUsersDeleted(String name) {
    return '$name gelöscht';
  }

  @override
  String adminUsersDeleteFailed(String name, String error) {
    return '$name konnte nicht gelöscht werden: $error';
  }

  @override
  String get orgSettingsTitle => 'Organisationseinstellungen';

  @override
  String get orgSettingsNoSettings => 'Keine Einstellungen';

  @override
  String get orgSettingsLoadError =>
      'Einstellungen konnten nicht geladen werden';

  @override
  String get orgSettingsSectionCompany => 'Unternehmen';

  @override
  String get orgSettingsSectionInvoiceDefaults => 'Rechnungsstandards';

  @override
  String get orgSettingsName => 'Name der Organisation';

  @override
  String get orgSettingsAddress => 'Adresse';

  @override
  String get orgSettingsPhone => 'Telefon';

  @override
  String get orgSettingsWebsite => 'Website';

  @override
  String get orgSettingsTaxId => 'Steuernummer';

  @override
  String get orgSettingsCurrency => 'Standardwährung';

  @override
  String get orgSettingsPaymentTerms => 'Zahlungsbedingungen';

  @override
  String get orgSettingsNumberPrefix => 'Rechnungsnummer-Präfix';

  @override
  String get orgSettingsGlAccount => 'Standard-Sachkonto';

  @override
  String get orgSettingsCostCenter => 'Standard-Kostenstelle';

  @override
  String get orgSettingsSave => 'Änderungen speichern';

  @override
  String get orgSettingsSaving => 'Wird gespeichert…';

  @override
  String orgSettingsFieldRequired(String label) {
    return '$label ist erforderlich';
  }

  @override
  String get orgSettingsSaved => 'Organisationseinstellungen gespeichert';

  @override
  String orgSettingsSaveFailed(String error) {
    return 'Speichern fehlgeschlagen: $error';
  }

  @override
  String get workflowsTitle => 'Workflows';

  @override
  String get workflowsEmpty => 'Keine Workflows gefunden';

  @override
  String get workflowsLoadError => 'Workflows konnten nicht geladen werden';

  @override
  String get workflowsStatusActive => 'Aktiv';

  @override
  String get workflowsStatusInactive => 'Inaktiv';

  @override
  String get workflowsDefault => 'Standard';

  @override
  String workflowsStepCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Schritte',
      one: '$count Schritt',
    );
    return '$_temp0';
  }

  @override
  String get workflowDetailFallbackTitle => 'Workflow';

  @override
  String get workflowDetailLoadError => 'Workflow konnte nicht geladen werden';

  @override
  String get workflowDetailNoSteps => 'Dieser Workflow hat keine Schritte.';

  @override
  String get workflowDetailDefaultWorkflow => 'Standard-Workflow';

  @override
  String workflowDetailStepNumber(int number) {
    return 'Schritt $number';
  }

  @override
  String get workflowDetailStepEnabled => 'Aktiviert';

  @override
  String get workflowDetailStepDisabled => 'Deaktiviert';

  @override
  String workflowDetailApproverCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Genehmiger',
      one: '$count Genehmiger',
    );
    return '$_temp0';
  }

  @override
  String workflowDetailDelaySummary(String hours) {
    return 'Verzögerung $hours Std.';
  }

  @override
  String workflowDetailConditionSummary(String field) {
    return 'Bei $field';
  }

  @override
  String get cashFlowTitle => 'Cashflow-Prognose';

  @override
  String cashFlowErrorPrefix(String error) {
    return 'Fehler: $error';
  }

  @override
  String cashFlowHorizonDays(int days) {
    return '$days Tage';
  }

  @override
  String get cashFlowLowBalanceAlert => 'Warnung: niedriger Kontostand';

  @override
  String cashFlowBreachSingle(
    String threshold,
    String period,
    String shortfall,
  ) {
    return 'Voraussichtlich unter dem Mindestsaldo von $threshold in $period (Unterdeckung $shortfall).';
  }

  @override
  String cashFlowBreachMultiple(int count, String period, String shortfall) {
    return '$count Zeiträume fallen voraussichtlich unter den Mindestsaldo. Schlimmster: $period, Unterdeckung $shortfall.';
  }

  @override
  String get cashFlowMinimum => 'Mindest';

  @override
  String get cashFlowOpeningBalance => 'Anfangssaldo';

  @override
  String get cashFlowProjectedEnd => 'Prognostiziertes Ende';

  @override
  String cashFlowProjectedEndSubtitle(int days) {
    return 'in $days Tagen';
  }

  @override
  String get cashFlowCommittedOut => 'Fest zugesagt';

  @override
  String get cashFlowCommittedSubtitle => 'feste Zusagen';

  @override
  String get cashFlowPendingOut => 'Ausstehend';

  @override
  String get cashFlowPendingSubtitle => 'laufende Pipeline';

  @override
  String get cashFlowOpeningSourceProvider => 'von Bank synchronisiert';

  @override
  String get cashFlowOpeningSourceSettings => 'gespeicherter Saldo';

  @override
  String get cashFlowOpeningSourceQuery => 'manuell';

  @override
  String get cashFlowOpeningSourceUnset => 'Saldo festlegen';

  @override
  String get cashFlowProjectedOutflows => 'Prognostizierte Abflüsse';

  @override
  String get cashFlowNoOutflows =>
      'Keine prognostizierten Abflüsse in diesem Zeitraum.';

  @override
  String cashFlowInvoiceCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Rechnungen',
      one: '$count Rechnung',
    );
    return '$_temp0';
  }

  @override
  String cashFlowCommittedAmount(String amount) {
    return 'zugesagt $amount';
  }

  @override
  String cashFlowPendingAmount(String amount) {
    return 'ausstehend $amount';
  }

  @override
  String get cashFlowPosition => 'Liquiditätsstatus';

  @override
  String get cashFlowNoPosition =>
      'Keine Liquiditätsprognose für diesen Zeitraum.';

  @override
  String cashFlowOutAmount(String amount) {
    return 'Abfluss $amount';
  }

  @override
  String cashFlowForecastRowLabel(
    String period,
    String scheduled,
    String committed,
    String pending,
    int count,
  ) {
    return '$period: geplant $scheduled, zugesagt $committed, ausstehend $pending, $count Rechnungen';
  }

  @override
  String cashFlowPositionRowLabel(
    String period,
    String opening,
    String outflow,
    String closing,
  ) {
    return '$period: Anfang $opening, Abfluss $outflow, Ende $closing';
  }

  @override
  String get cashFlowBelowThresholdSuffix => ', unter dem Schwellenwert';

  @override
  String cashFlowLowBalanceAlertLabel(String message) {
    return 'Warnung: niedriger Kontostand. $message';
  }

  @override
  String get contractsTitle => 'Verträge';

  @override
  String get contractsSearchHint => 'Verträge suchen …';

  @override
  String get contractsEmpty => 'Keine Verträge gefunden';

  @override
  String get contractsFilterDraft => 'Entwurf';

  @override
  String get contractsFilterActive => 'Aktiv';

  @override
  String get contractsFilterExpired => 'Abgelaufen';

  @override
  String get contractsFilterTerminated => 'Gekündigt';

  @override
  String get contractsFilterCancelled => 'Storniert';

  @override
  String get contractDetailTitle => 'Vertragsdetails';

  @override
  String contractDetailErrorPrefix(String error) {
    return 'Fehler: $error';
  }

  @override
  String get contractDetailUntitled => 'Unbenannter Vertrag';

  @override
  String get contractDetailFieldContractNumber => 'Vertragsnr.';

  @override
  String get contractDetailFieldVendor => 'Lieferant';

  @override
  String get contractDetailFieldType => 'Typ';

  @override
  String get contractDetailFieldCurrency => 'Währung';

  @override
  String get contractDetailFieldSpendLimit => 'Ausgabenlimit';

  @override
  String get contractDetailNotToExceed => ' (nicht zu überschreiten)';

  @override
  String get contractDetailFieldStartDate => 'Startdatum';

  @override
  String get contractDetailFieldEndDate => 'Enddatum';

  @override
  String get contractDetailFieldSigned => 'Unterzeichnet';

  @override
  String get contractDetailFieldAutoRenew => 'Automatische Verlängerung';

  @override
  String get contractDetailYes => 'Ja';

  @override
  String get contractDetailNo => 'Nein';

  @override
  String get contractDetailFieldRenewalTerm => 'Verlängerungslaufzeit';

  @override
  String contractDetailRenewalTermMonths(int months) {
    return '$months Monate';
  }

  @override
  String get contractDetailFieldRenewalNotice => 'Kündigungsfrist';

  @override
  String contractDetailRenewalNoticeDays(int days) {
    return '$days Tage';
  }

  @override
  String get contractDetailFieldPaymentTerms => 'Zahlungsbedingungen';

  @override
  String get contractDetailFieldDescription => 'Beschreibung';

  @override
  String get contractDetailFieldCreated => 'Erstellt';

  @override
  String get contractDetailSectionSpend => 'Ausgaben';

  @override
  String get contractDetailSectionLineItems => 'Positionen';

  @override
  String get contractDetailSpendInvoiced => 'In Rechnung gestellt';

  @override
  String contractDetailSpendInvoiceCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Rechnungen',
      one: '$count Rechnung',
    );
    return '$_temp0';
  }

  @override
  String get contractDetailSpendOverLimit => 'Über Limit';

  @override
  String get contractDetailSpendRemaining => 'Verbleibend';

  @override
  String contractDetailSpendOfLimit(String limit) {
    return 'von $limit';
  }

  @override
  String get contractDetailSpendNoLimit => 'kein Limit festgelegt';

  @override
  String get contractDetailLineItemFallback => 'Position';

  @override
  String contractDetailLineQty(String quantity) {
    return 'Menge $quantity';
  }

  @override
  String contractDetailLineUnitPrice(String price) {
    return '@ $price';
  }

  @override
  String contractDetailLineGl(String account) {
    return 'Sachkonto $account';
  }

  @override
  String get contractActivate => 'Aktivieren';

  @override
  String get contractActivated => 'Vertrag aktiviert';

  @override
  String get contractActivateFailed =>
      'Vertrag konnte nicht aktiviert werden – bitte erneut versuchen';

  @override
  String get contractTerminate => 'Kündigen';

  @override
  String get contractTerminateTitle => 'Vertrag kündigen';

  @override
  String get contractTerminateBody =>
      'Damit wird der Vertrag vorzeitig beendet. Dies kann nicht rückgängig gemacht werden. Fortfahren?';

  @override
  String get contractTerminated => 'Vertrag gekündigt';

  @override
  String get contractTerminateFailed =>
      'Vertrag konnte nicht gekündigt werden – bitte erneut versuchen';

  @override
  String get exceptionDetailTitle => 'Ausnahme';

  @override
  String get exceptionDetailNotFound => 'Ausnahme nicht gefunden';

  @override
  String get exceptionDetailOverdue => 'Überfällig';

  @override
  String get exceptionDetailSectionDescription => 'Beschreibung';

  @override
  String get exceptionDetailSectionInvoice => 'Rechnung';

  @override
  String get exceptionDetailNoLinkedInvoice => 'Keine verknüpfte Rechnung';

  @override
  String get exceptionDetailFieldNumber => 'Nummer';

  @override
  String get exceptionDetailFieldVendor => 'Lieferant';

  @override
  String get exceptionDetailFieldAmount => 'Betrag';

  @override
  String get exceptionDetailFieldSeverity => 'Schweregrad';

  @override
  String get exceptionDetailSectionSla => 'SLA';

  @override
  String get exceptionDetailFieldCreated => 'Erstellt';

  @override
  String get exceptionDetailFieldDue => 'Fällig';

  @override
  String get exceptionDetailNoSla => 'Kein SLA festgelegt';

  @override
  String get exceptionDetailFieldStatus => 'Status';

  @override
  String get exceptionDetailOnTrack => 'Im Plan';

  @override
  String get exceptionDetailResolvedIn => 'Gelöst in';

  @override
  String exceptionDetailResolvedInHours(String hours) {
    return '$hours Std.';
  }

  @override
  String get exceptionDetailSectionAssignee => 'Zuständig';

  @override
  String get exceptionDetailUnassigned => 'Nicht zugewiesen';

  @override
  String get exceptionDetailAssign => 'Zuweisen';

  @override
  String get exceptionDetailReassign => 'Neu zuweisen';

  @override
  String get exceptionDetailSectionResolution => 'Lösung';

  @override
  String get exceptionDetailResolutionNote => 'Notiz';

  @override
  String get exceptionDetailResolutionBy => 'Von';

  @override
  String get exceptionDetailResolutionAt => 'Am';

  @override
  String get exceptionDetailActionResolved => 'Ausnahme gelöst';

  @override
  String get exceptionDetailActionEscalated => 'Ausnahme eskaliert';

  @override
  String get exceptionDetailActionDismissed => 'Ausnahme verworfen';

  @override
  String get exceptionDetailActionResolveFailed =>
      'Ausnahme konnte nicht gelöst werden';

  @override
  String get exceptionDetailActionEscalateFailed =>
      'Ausnahme konnte nicht eskaliert werden';

  @override
  String get exceptionDetailActionDismissFailed =>
      'Ausnahme konnte nicht verworfen werden';

  @override
  String get exceptionDetailAssignTo => 'Zuweisen an';

  @override
  String get exceptionDetailUnassign => 'Zuweisung aufheben';

  @override
  String exceptionDetailLoadUsersFailed(String error) {
    return 'Benutzer konnten nicht geladen werden: $error';
  }

  @override
  String get exceptionDetailAssigneeUpdateFailed =>
      'Zuständigkeit konnte nicht aktualisiert werden';

  @override
  String get exceptionDetailUnassigned2 => 'Zuweisung der Ausnahme aufgehoben';

  @override
  String exceptionDetailAssignedTo(String name) {
    return 'Zugewiesen an $name';
  }
}
