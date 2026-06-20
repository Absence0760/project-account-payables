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
  String get commonClose => 'Close';

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

  @override
  String get notificationsTitle => 'Notifications';

  @override
  String get notificationsMarkAllRead => 'Mark all read';

  @override
  String get notificationsMarkAllReadLabel => 'Mark all notifications read';

  @override
  String get notificationsFilterUnread => 'Unread';

  @override
  String get notificationsAllMarkedRead => 'All notifications marked read';

  @override
  String get notificationsCouldNotMarkAll => 'Could not mark all read';

  @override
  String get notificationsEmptyUnread => 'No unread notifications';

  @override
  String get notificationsEmpty => 'No notifications';

  @override
  String get notificationsCaughtUp => 'You\'re all caught up';

  @override
  String get notificationsNothingYet => 'Nothing here yet';

  @override
  String get notificationsLoadError => 'Couldn\'t load notifications';

  @override
  String get vendorsTitle => 'Vendors';

  @override
  String get vendorsSyncErp => 'Sync from ERP';

  @override
  String get vendorsSyncErpLabel => 'Sync vendors from ERP';

  @override
  String get vendorsSearchHint => 'Search vendors...';

  @override
  String get vendorsFilterUnverified => 'Unverified';

  @override
  String get vendorsFilterActive => 'Active';

  @override
  String get vendorsFilterInactive => 'Inactive';

  @override
  String get vendorsFilterRejected => 'Rejected';

  @override
  String get vendorsEmpty => 'No vendors found';

  @override
  String get vendorsLoadError => 'Could not load vendors';

  @override
  String get vendorActionVerify => 'Verify';

  @override
  String get vendorActionReject => 'Reject';

  @override
  String get vendorUnverifiedLabel => 'Unverified vendor';

  @override
  String get vendorVerifyHint => 'Make eligible for payment';

  @override
  String get vendorRejectHint => 'Mark as invalid / duplicate';

  @override
  String get vendorVerified => 'Vendor verified';

  @override
  String get vendorRejected => 'Vendor rejected';

  @override
  String get vendorActionFailed => 'Action failed';

  @override
  String vendorSyncFailed(String error) {
    return 'ERP sync failed: $error';
  }

  @override
  String get exceptionsTitle => 'Exceptions';

  @override
  String get exceptionsFilterOpen => 'Open';

  @override
  String get exceptionsFilterEscalated => 'Escalated';

  @override
  String get exceptionsFilterResolved => 'Resolved';

  @override
  String get exceptionsFilterDismissed => 'Dismissed';

  @override
  String get exceptionsEmpty => 'No exceptions';

  @override
  String get exceptionsQueueClear => 'The exception queue is clear';

  @override
  String get exceptionActionResolve => 'Resolve';

  @override
  String get exceptionActionEscalate => 'Escalate';

  @override
  String get exceptionActionDismiss => 'Dismiss';

  @override
  String get exceptionResolved => 'Exception resolved';

  @override
  String get exceptionEscalated => 'Exception escalated';

  @override
  String get exceptionDismissed => 'Exception dismissed';

  @override
  String get exceptionActionFailed => 'Action failed';

  @override
  String get paymentsTitle => 'Payments';

  @override
  String get paymentsEmpty => 'No payments';

  @override
  String paymentsErrorPrefix(String error) {
    return 'Error: $error';
  }

  @override
  String get paymentStatusPending => 'Pending';

  @override
  String get paymentStatusProcessing => 'Processing';

  @override
  String get paymentStatusCompleted => 'Completed';

  @override
  String get paymentStatusFailed => 'Failed';

  @override
  String get paymentStatusCancelled => 'Cancelled';

  @override
  String get approvalsTitle => 'Pending Approvals';

  @override
  String get approvalsAllCaughtUp => 'All caught up!';

  @override
  String get approvalsNoneWaiting => 'No invoices waiting for approval';

  @override
  String approvalsPendingCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count invoices pending',
      one: '$count invoice pending',
    );
    return '$_temp0';
  }

  @override
  String get approvalActionApprove => 'Approve';

  @override
  String get approvalActionReject => 'Reject';

  @override
  String get approvalApproved => 'Invoice approved';

  @override
  String get captureTitle => 'Capture Invoice';

  @override
  String get captureChange => 'Change';

  @override
  String get captureUpload => 'Upload';

  @override
  String get captureUploading => 'Uploading…';

  @override
  String get captureEmptyPrompt =>
      'Take a photo, choose from gallery, or pick a file';

  @override
  String get captureCamera => 'Camera';

  @override
  String get captureGallery => 'Gallery';

  @override
  String get captureChooseFile => 'Choose file';

  @override
  String get captureSupportedFormats => 'Supports PDF, PNG, JPG and TIFF';

  @override
  String get captureUploadSuccess => 'Invoice uploaded successfully';

  @override
  String captureUploadFailedStatus(int status, String message) {
    return 'Upload failed ($status): $message';
  }

  @override
  String captureUploadFailed(String error) {
    return 'Upload failed: $error';
  }

  @override
  String captureSelectedDocument(String name) {
    return 'Selected document: $name';
  }

  @override
  String get capturePdfReady => 'PDF document ready to upload';

  @override
  String get advSearchTitle => 'Advanced Search';

  @override
  String get advSearchClose => 'Close advanced search';

  @override
  String get advSearchVendor => 'Vendor';

  @override
  String get advSearchPoNumber => 'PO Number';

  @override
  String get advSearchMinAmount => 'Min amount';

  @override
  String get advSearchMaxAmount => 'Max amount';

  @override
  String get advSearchDueFrom => 'Due from';

  @override
  String get advSearchDueTo => 'Due to';

  @override
  String get advSearchAny => 'Any';

  @override
  String get advSearchInvalidAmount => 'Enter a valid amount (e.g. 1000)';

  @override
  String get advSearchMinMaxError => 'Min must not exceed max';

  @override
  String advSearchClearField(String label) {
    return 'Clear $label';
  }

  @override
  String advSearchDateFieldHint(String label, String value) {
    return '$label, currently $value. Double tap to change.';
  }
}
