// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Japanese (`ja`).
class AppLocalizationsJa extends AppLocalizations {
  AppLocalizationsJa([String locale = 'ja']) : super(locale);

  @override
  String get navDashboard => 'ダッシュボード';

  @override
  String get navInvoices => '請求書';

  @override
  String get navContracts => '契約';

  @override
  String get navApprovals => '承認';

  @override
  String get navExceptions => '例外';

  @override
  String get navVendors => '取引先';

  @override
  String get navPay => '支払';

  @override
  String get navPayments => '支払い';

  @override
  String get navSettings => '設定';

  @override
  String get shellAppName => 'Account Payables';

  @override
  String get commonSave => '保存';

  @override
  String get commonSaving => '保存中…';

  @override
  String get commonCancel => 'キャンセル';

  @override
  String get commonLoading => '読み込み中…';

  @override
  String get commonRetry => '再試行';

  @override
  String get commonAll => 'すべて';

  @override
  String get commonSearch => '検索';

  @override
  String get commonClear => 'クリア';

  @override
  String get commonApply => '適用';

  @override
  String get settingsTitle => '設定';

  @override
  String get settingsTenant => 'テナント';

  @override
  String get settingsTenantNotSet => '未設定';

  @override
  String get settingsApiServer => 'APIサーバー';

  @override
  String get settingsBiometricUnlock => '生体認証ロック解除';

  @override
  String get settingsBiometricHint => '指紋または顔でロックを解除します';

  @override
  String get settingsSignOut => 'サインアウト';

  @override
  String get settingsLanguage => '言語';

  @override
  String get settingsLanguageHint => 'アプリ全体で使用する言語を選択してください。選択内容はこの端末に保存されます。';

  @override
  String get settingsLanguageSystem => 'システムの既定値';

  @override
  String get dashboardTitle => 'ダッシュボード';

  @override
  String get dashboardTotalInvoices => '請求書合計';

  @override
  String get dashboardUpcoming => '予定';

  @override
  String get dashboardForReview => '確認待ち';

  @override
  String get dashboardApproved => '承認済み';

  @override
  String get dashboardAging => '請求書の経過日数';

  @override
  String get dashboardTopVendors => '上位の取引先';

  @override
  String get dashboardAgingCurrent => '期限内';

  @override
  String get dashboardAgingDays30 => '30日';

  @override
  String get dashboardAgingDays60 => '60日';

  @override
  String get dashboardAgingDays90plus => '90日以上';

  @override
  String get dashboardCachedBanner => 'キャッシュデータを表示中 — サーバーに接続できませんでした';

  @override
  String dashboardErrorPrefix(String error) {
    return 'エラー: $error';
  }

  @override
  String dashboardInvoiceCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count件の請求書',
    );
    return '$_temp0';
  }

  @override
  String get invoicesTitle => '請求書';

  @override
  String get invoicesSearchHint => '請求書を検索…';

  @override
  String get invoicesSearchAria => '請求書を検索';

  @override
  String get invoicesAdvancedSearch => '詳細検索';

  @override
  String get invoicesAdvancedSearchActive => '詳細検索、フィルター適用中';

  @override
  String get invoicesCaptureInvoice => '請求書を撮影';

  @override
  String get invoicesCaptureInvoiceLabel => '請求書を撮影';

  @override
  String get invoicesEmpty => '請求書が見つかりません';

  @override
  String get invoicesFilterAll => 'すべて';

  @override
  String get invoicesFilterNew => '新規';

  @override
  String get invoicesFilterPending => '保留中';

  @override
  String get invoicesFilterReview => '確認';

  @override
  String get invoicesFilterApproved => '承認済み';

  @override
  String get invoicesFilterRejected => '却下';

  @override
  String get invoicesFilterPaid => '支払済み';

  @override
  String get invoicesColInvoiceNumber => '請求書番号';

  @override
  String get invoicesColVendor => '取引先';

  @override
  String get invoicesColAmount => '金額';

  @override
  String get invoicesColDueDate => '支払期限';

  @override
  String get invoicesColStatus => 'ステータス';
}
