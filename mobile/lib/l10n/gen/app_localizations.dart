import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_de.dart';
import 'app_localizations_en.dart';
import 'app_localizations_es.dart';
import 'app_localizations_fr.dart';
import 'app_localizations_ja.dart';
import 'app_localizations_pt.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'gen/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations)!;
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
        delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[
    Locale('de'),
    Locale('en'),
    Locale('es'),
    Locale('fr'),
    Locale('ja'),
    Locale('pt'),
    Locale('pt', 'BR'),
  ];

  /// No description provided for @navDashboard.
  ///
  /// In en, this message translates to:
  /// **'Dashboard'**
  String get navDashboard;

  /// No description provided for @navInvoices.
  ///
  /// In en, this message translates to:
  /// **'Invoices'**
  String get navInvoices;

  /// No description provided for @navContracts.
  ///
  /// In en, this message translates to:
  /// **'Contracts'**
  String get navContracts;

  /// No description provided for @navApprovals.
  ///
  /// In en, this message translates to:
  /// **'Approvals'**
  String get navApprovals;

  /// No description provided for @navExceptions.
  ///
  /// In en, this message translates to:
  /// **'Exceptions'**
  String get navExceptions;

  /// No description provided for @navVendors.
  ///
  /// In en, this message translates to:
  /// **'Vendors'**
  String get navVendors;

  /// No description provided for @navPay.
  ///
  /// In en, this message translates to:
  /// **'Pay'**
  String get navPay;

  /// No description provided for @navPayments.
  ///
  /// In en, this message translates to:
  /// **'Payments'**
  String get navPayments;

  /// No description provided for @navSettings.
  ///
  /// In en, this message translates to:
  /// **'Settings'**
  String get navSettings;

  /// No description provided for @shellAppName.
  ///
  /// In en, this message translates to:
  /// **'Account Payables'**
  String get shellAppName;

  /// No description provided for @commonSave.
  ///
  /// In en, this message translates to:
  /// **'Save'**
  String get commonSave;

  /// No description provided for @commonSaving.
  ///
  /// In en, this message translates to:
  /// **'Saving…'**
  String get commonSaving;

  /// No description provided for @commonCancel.
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get commonCancel;

  /// No description provided for @commonLoading.
  ///
  /// In en, this message translates to:
  /// **'Loading…'**
  String get commonLoading;

  /// No description provided for @commonRetry.
  ///
  /// In en, this message translates to:
  /// **'Retry'**
  String get commonRetry;

  /// No description provided for @commonAll.
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get commonAll;

  /// No description provided for @commonSearch.
  ///
  /// In en, this message translates to:
  /// **'Search'**
  String get commonSearch;

  /// No description provided for @commonClear.
  ///
  /// In en, this message translates to:
  /// **'Clear'**
  String get commonClear;

  /// No description provided for @commonApply.
  ///
  /// In en, this message translates to:
  /// **'Apply'**
  String get commonApply;

  /// No description provided for @settingsTitle.
  ///
  /// In en, this message translates to:
  /// **'Settings'**
  String get settingsTitle;

  /// No description provided for @settingsTenant.
  ///
  /// In en, this message translates to:
  /// **'Tenant'**
  String get settingsTenant;

  /// No description provided for @settingsTenantNotSet.
  ///
  /// In en, this message translates to:
  /// **'Not set'**
  String get settingsTenantNotSet;

  /// No description provided for @settingsApiServer.
  ///
  /// In en, this message translates to:
  /// **'API Server'**
  String get settingsApiServer;

  /// No description provided for @settingsBiometricUnlock.
  ///
  /// In en, this message translates to:
  /// **'Biometric Unlock'**
  String get settingsBiometricUnlock;

  /// No description provided for @settingsBiometricHint.
  ///
  /// In en, this message translates to:
  /// **'Use fingerprint or face to unlock'**
  String get settingsBiometricHint;

  /// No description provided for @settingsSignOut.
  ///
  /// In en, this message translates to:
  /// **'Sign Out'**
  String get settingsSignOut;

  /// No description provided for @settingsLanguage.
  ///
  /// In en, this message translates to:
  /// **'Language'**
  String get settingsLanguage;

  /// No description provided for @settingsLanguageHint.
  ///
  /// In en, this message translates to:
  /// **'Choose the language used across the app. Your choice is saved on this device.'**
  String get settingsLanguageHint;

  /// No description provided for @settingsLanguageSystem.
  ///
  /// In en, this message translates to:
  /// **'System default'**
  String get settingsLanguageSystem;

  /// No description provided for @dashboardTitle.
  ///
  /// In en, this message translates to:
  /// **'Dashboard'**
  String get dashboardTitle;

  /// No description provided for @dashboardTotalInvoices.
  ///
  /// In en, this message translates to:
  /// **'Total Invoices'**
  String get dashboardTotalInvoices;

  /// No description provided for @dashboardUpcoming.
  ///
  /// In en, this message translates to:
  /// **'Upcoming'**
  String get dashboardUpcoming;

  /// No description provided for @dashboardForReview.
  ///
  /// In en, this message translates to:
  /// **'For Review'**
  String get dashboardForReview;

  /// No description provided for @dashboardApproved.
  ///
  /// In en, this message translates to:
  /// **'Approved'**
  String get dashboardApproved;

  /// No description provided for @dashboardAging.
  ///
  /// In en, this message translates to:
  /// **'Invoice Aging'**
  String get dashboardAging;

  /// No description provided for @dashboardTopVendors.
  ///
  /// In en, this message translates to:
  /// **'Top Vendors'**
  String get dashboardTopVendors;

  /// No description provided for @dashboardAgingCurrent.
  ///
  /// In en, this message translates to:
  /// **'Current'**
  String get dashboardAgingCurrent;

  /// No description provided for @dashboardAgingDays30.
  ///
  /// In en, this message translates to:
  /// **'30 Days'**
  String get dashboardAgingDays30;

  /// No description provided for @dashboardAgingDays60.
  ///
  /// In en, this message translates to:
  /// **'60 Days'**
  String get dashboardAgingDays60;

  /// No description provided for @dashboardAgingDays90plus.
  ///
  /// In en, this message translates to:
  /// **'90+'**
  String get dashboardAgingDays90plus;

  /// No description provided for @dashboardCachedBanner.
  ///
  /// In en, this message translates to:
  /// **'Showing cached data — couldn\'t reach the server'**
  String get dashboardCachedBanner;

  /// No description provided for @dashboardErrorPrefix.
  ///
  /// In en, this message translates to:
  /// **'Error: {error}'**
  String dashboardErrorPrefix(String error);

  /// No description provided for @dashboardInvoiceCount.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, one {{count} invoice} other {{count} invoices}}'**
  String dashboardInvoiceCount(int count);

  /// No description provided for @invoicesTitle.
  ///
  /// In en, this message translates to:
  /// **'Invoices'**
  String get invoicesTitle;

  /// No description provided for @invoicesSearchHint.
  ///
  /// In en, this message translates to:
  /// **'Search invoices...'**
  String get invoicesSearchHint;

  /// No description provided for @invoicesSearchAria.
  ///
  /// In en, this message translates to:
  /// **'Search invoices'**
  String get invoicesSearchAria;

  /// No description provided for @invoicesAdvancedSearch.
  ///
  /// In en, this message translates to:
  /// **'Advanced Search'**
  String get invoicesAdvancedSearch;

  /// No description provided for @invoicesAdvancedSearchActive.
  ///
  /// In en, this message translates to:
  /// **'Advanced search, filters active'**
  String get invoicesAdvancedSearchActive;

  /// No description provided for @invoicesCaptureInvoice.
  ///
  /// In en, this message translates to:
  /// **'Capture Invoice'**
  String get invoicesCaptureInvoice;

  /// No description provided for @invoicesCaptureInvoiceLabel.
  ///
  /// In en, this message translates to:
  /// **'Capture invoice'**
  String get invoicesCaptureInvoiceLabel;

  /// No description provided for @invoicesEmpty.
  ///
  /// In en, this message translates to:
  /// **'No invoices found'**
  String get invoicesEmpty;

  /// No description provided for @invoicesFilterAll.
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get invoicesFilterAll;

  /// No description provided for @invoicesFilterNew.
  ///
  /// In en, this message translates to:
  /// **'New'**
  String get invoicesFilterNew;

  /// No description provided for @invoicesFilterPending.
  ///
  /// In en, this message translates to:
  /// **'Pending'**
  String get invoicesFilterPending;

  /// No description provided for @invoicesFilterReview.
  ///
  /// In en, this message translates to:
  /// **'Review'**
  String get invoicesFilterReview;

  /// No description provided for @invoicesFilterApproved.
  ///
  /// In en, this message translates to:
  /// **'Approved'**
  String get invoicesFilterApproved;

  /// No description provided for @invoicesFilterRejected.
  ///
  /// In en, this message translates to:
  /// **'Rejected'**
  String get invoicesFilterRejected;

  /// No description provided for @invoicesFilterPaid.
  ///
  /// In en, this message translates to:
  /// **'Paid'**
  String get invoicesFilterPaid;

  /// No description provided for @invoicesColInvoiceNumber.
  ///
  /// In en, this message translates to:
  /// **'Invoice #'**
  String get invoicesColInvoiceNumber;

  /// No description provided for @invoicesColVendor.
  ///
  /// In en, this message translates to:
  /// **'Vendor'**
  String get invoicesColVendor;

  /// No description provided for @invoicesColAmount.
  ///
  /// In en, this message translates to:
  /// **'Amount'**
  String get invoicesColAmount;

  /// No description provided for @invoicesColDueDate.
  ///
  /// In en, this message translates to:
  /// **'Due Date'**
  String get invoicesColDueDate;

  /// No description provided for @invoicesColStatus.
  ///
  /// In en, this message translates to:
  /// **'Status'**
  String get invoicesColStatus;
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) => <String>[
    'de',
    'en',
    'es',
    'fr',
    'ja',
    'pt',
  ].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when language+country codes are specified.
  switch (locale.languageCode) {
    case 'pt':
      {
        switch (locale.countryCode) {
          case 'BR':
            return AppLocalizationsPtBr();
        }
        break;
      }
  }

  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'de':
      return AppLocalizationsDe();
    case 'en':
      return AppLocalizationsEn();
    case 'es':
      return AppLocalizationsEs();
    case 'fr':
      return AppLocalizationsFr();
    case 'ja':
      return AppLocalizationsJa();
    case 'pt':
      return AppLocalizationsPt();
  }

  throw FlutterError(
    'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
