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
  String get shellAppName => 'Account Payables';

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
}
