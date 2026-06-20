// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get navDashboard => 'Dashboard';

  @override
  String get navInvoices => 'Invoices';

  @override
  String get navContracts => 'Contracts';

  @override
  String get navApprovals => 'Approvals';

  @override
  String get navExceptions => 'Exceptions';

  @override
  String get navVendors => 'Vendors';

  @override
  String get navPay => 'Pay';

  @override
  String get navPayments => 'Payments';

  @override
  String get navSettings => 'Settings';

  @override
  String get shellAppName => 'Account Payables';

  @override
  String get commonSave => 'Save';

  @override
  String get commonSaving => 'Saving…';

  @override
  String get commonCancel => 'Cancel';

  @override
  String get commonLoading => 'Loading…';

  @override
  String get commonRetry => 'Retry';

  @override
  String get commonAll => 'All';

  @override
  String get commonSearch => 'Search';

  @override
  String get commonClear => 'Clear';

  @override
  String get commonApply => 'Apply';

  @override
  String get settingsTitle => 'Settings';

  @override
  String get settingsTenant => 'Tenant';

  @override
  String get settingsTenantNotSet => 'Not set';

  @override
  String get settingsApiServer => 'API Server';

  @override
  String get settingsBiometricUnlock => 'Biometric Unlock';

  @override
  String get settingsBiometricHint => 'Use fingerprint or face to unlock';

  @override
  String get settingsSignOut => 'Sign Out';

  @override
  String get settingsLanguage => 'Language';

  @override
  String get settingsLanguageHint =>
      'Choose the language used across the app. Your choice is saved on this device.';

  @override
  String get settingsLanguageSystem => 'System default';

  @override
  String get dashboardTitle => 'Dashboard';

  @override
  String get dashboardTotalInvoices => 'Total Invoices';

  @override
  String get dashboardUpcoming => 'Upcoming';

  @override
  String get dashboardForReview => 'For Review';

  @override
  String get dashboardApproved => 'Approved';

  @override
  String get dashboardAging => 'Invoice Aging';

  @override
  String get dashboardTopVendors => 'Top Vendors';

  @override
  String get dashboardAgingCurrent => 'Current';

  @override
  String get dashboardAgingDays30 => '30 Days';

  @override
  String get dashboardAgingDays60 => '60 Days';

  @override
  String get dashboardAgingDays90plus => '90+';

  @override
  String get dashboardCachedBanner =>
      'Showing cached data — couldn\'t reach the server';

  @override
  String dashboardErrorPrefix(String error) {
    return 'Error: $error';
  }

  @override
  String dashboardInvoiceCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count invoices',
      one: '$count invoice',
    );
    return '$_temp0';
  }

  @override
  String get invoicesTitle => 'Invoices';

  @override
  String get invoicesSearchHint => 'Search invoices...';

  @override
  String get invoicesSearchAria => 'Search invoices';

  @override
  String get invoicesAdvancedSearch => 'Advanced Search';

  @override
  String get invoicesAdvancedSearchActive => 'Advanced search, filters active';

  @override
  String get invoicesCaptureInvoice => 'Capture Invoice';

  @override
  String get invoicesCaptureInvoiceLabel => 'Capture invoice';

  @override
  String get invoicesEmpty => 'No invoices found';

  @override
  String get invoicesFilterAll => 'All';

  @override
  String get invoicesFilterNew => 'New';

  @override
  String get invoicesFilterPending => 'Pending';

  @override
  String get invoicesFilterReview => 'Review';

  @override
  String get invoicesFilterApproved => 'Approved';

  @override
  String get invoicesFilterRejected => 'Rejected';

  @override
  String get invoicesFilterPaid => 'Paid';

  @override
  String get invoicesColInvoiceNumber => 'Invoice #';

  @override
  String get invoicesColVendor => 'Vendor';

  @override
  String get invoicesColAmount => 'Amount';

  @override
  String get invoicesColDueDate => 'Due Date';

  @override
  String get invoicesColStatus => 'Status';
}
