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

  @override
  String get notificationsTitle => '通知';

  @override
  String get notificationsMarkAllRead => 'すべて既読にする';

  @override
  String get notificationsMarkAllReadLabel => 'すべての通知を既読にする';

  @override
  String get notificationsFilterUnread => '未読';

  @override
  String get notificationsAllMarkedRead => 'すべての通知を既読にしました';

  @override
  String get notificationsCouldNotMarkAll => 'すべてを既読にできませんでした';

  @override
  String get notificationsEmptyUnread => '未読の通知はありません';

  @override
  String get notificationsEmpty => '通知はありません';

  @override
  String get notificationsCaughtUp => 'すべて確認済みです';

  @override
  String get notificationsNothingYet => 'まだ何もありません';

  @override
  String get notificationsLoadError => '通知を読み込めませんでした';

  @override
  String get vendorsTitle => '取引先';

  @override
  String get vendorsSyncErp => 'ERPから同期';

  @override
  String get vendorsSyncErpLabel => 'ERPから取引先を同期';

  @override
  String get vendorsSearchHint => '取引先を検索…';

  @override
  String get vendorsFilterUnverified => '未確認';

  @override
  String get vendorsFilterActive => '有効';

  @override
  String get vendorsFilterInactive => '無効';

  @override
  String get vendorsFilterRejected => '却下';

  @override
  String get vendorsEmpty => '取引先が見つかりません';

  @override
  String get vendorsLoadError => '取引先を読み込めませんでした';

  @override
  String get vendorActionVerify => '確認';

  @override
  String get vendorActionReject => '却下';

  @override
  String get vendorUnverifiedLabel => '未確認の取引先';

  @override
  String get vendorVerifyHint => '支払い対象にする';

  @override
  String get vendorRejectHint => '無効／重複としてマーク';

  @override
  String get vendorVerified => '取引先を確認しました';

  @override
  String get vendorRejected => '取引先を却下しました';

  @override
  String get vendorActionFailed => '操作に失敗しました';

  @override
  String vendorSyncFailed(String error) {
    return 'ERP同期に失敗しました: $error';
  }

  @override
  String get exceptionsTitle => '例外';

  @override
  String get exceptionsFilterOpen => '未処理';

  @override
  String get exceptionsFilterEscalated => 'エスカレーション済み';

  @override
  String get exceptionsFilterResolved => '解決済み';

  @override
  String get exceptionsFilterDismissed => '却下済み';

  @override
  String get exceptionsEmpty => '例外はありません';

  @override
  String get exceptionsQueueClear => '例外キューは空です';

  @override
  String get exceptionActionResolve => '解決';

  @override
  String get exceptionActionEscalate => 'エスカレーション';

  @override
  String get exceptionActionDismiss => '却下';

  @override
  String get exceptionResolved => '例外を解決しました';

  @override
  String get exceptionEscalated => '例外をエスカレーションしました';

  @override
  String get exceptionDismissed => '例外を却下しました';

  @override
  String get exceptionActionFailed => '操作に失敗しました';

  @override
  String get paymentsTitle => '支払い';

  @override
  String get paymentsEmpty => '支払いはありません';

  @override
  String paymentsErrorPrefix(String error) {
    return 'エラー: $error';
  }

  @override
  String get paymentStatusPending => '保留中';

  @override
  String get paymentStatusProcessing => '処理中';

  @override
  String get paymentStatusCompleted => '完了';

  @override
  String get paymentStatusFailed => '失敗';

  @override
  String get paymentStatusCancelled => 'キャンセル済み';
}
