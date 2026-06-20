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
  String get commonClose => '閉じる';

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

  @override
  String get approvalsTitle => '承認待ち';

  @override
  String get approvalsAllCaughtUp => 'すべて完了しました！';

  @override
  String get approvalsNoneWaiting => '承認待ちの請求書はありません';

  @override
  String approvalsPendingCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count件の請求書が承認待ち',
    );
    return '$_temp0';
  }

  @override
  String get approvalActionApprove => '承認';

  @override
  String get approvalActionReject => '却下';

  @override
  String get approvalApproved => '請求書を承認しました';

  @override
  String get captureTitle => '請求書を撮影';

  @override
  String get captureChange => '変更';

  @override
  String get captureUpload => 'アップロード';

  @override
  String get captureUploading => 'アップロード中…';

  @override
  String get captureEmptyPrompt => '写真を撮影、ギャラリーから選択、またはファイルを選択してください';

  @override
  String get captureCamera => 'カメラ';

  @override
  String get captureGallery => 'ギャラリー';

  @override
  String get captureChooseFile => 'ファイルを選択';

  @override
  String get captureSupportedFormats => 'PDF、PNG、JPG、TIFFに対応';

  @override
  String get captureUploadSuccess => '請求書を正常にアップロードしました';

  @override
  String captureUploadFailedStatus(int status, String message) {
    return 'アップロードに失敗しました（$status）: $message';
  }

  @override
  String captureUploadFailed(String error) {
    return 'アップロードに失敗しました: $error';
  }

  @override
  String captureSelectedDocument(String name) {
    return '選択した書類: $name';
  }

  @override
  String get capturePdfReady => 'PDF書類をアップロードする準備ができました';

  @override
  String get advSearchTitle => '詳細検索';

  @override
  String get advSearchClose => '詳細検索を閉じる';

  @override
  String get advSearchVendor => '取引先';

  @override
  String get advSearchPoNumber => '発注番号';

  @override
  String get advSearchMinAmount => '最小金額';

  @override
  String get advSearchMaxAmount => '最大金額';

  @override
  String get advSearchDueFrom => '期限開始';

  @override
  String get advSearchDueTo => '期限終了';

  @override
  String get advSearchAny => '指定なし';

  @override
  String get advSearchInvalidAmount => '有効な金額を入力してください（例: 1000）';

  @override
  String get advSearchMinMaxError => '最小値は最大値を超えてはいけません';

  @override
  String advSearchClearField(String label) {
    return '$labelをクリア';
  }

  @override
  String advSearchDateFieldHint(String label, String value) {
    return '$label、現在 $value。ダブルタップで変更します。';
  }

  @override
  String get invoiceDetailTitle => '請求書の詳細';

  @override
  String get invoiceDetailEdit => '編集';

  @override
  String get invoiceDetailEditLabel => '請求書を編集';

  @override
  String get invoiceDetailRetry => '再試行';

  @override
  String invoiceDetailErrorPrefix(String error) {
    return 'エラー: $error';
  }

  @override
  String get invoiceDetailNoChanges => '保存する変更はありません';

  @override
  String get invoiceDetailUpdated => '請求書を更新しました';

  @override
  String get invoiceDetailUpdateFailed => '変更を保存できませんでした。もう一度お試しください';

  @override
  String get invoiceDetailApproved => '請求書を承認しました';

  @override
  String get invoiceDetailApproveFailed => '請求書を承認できませんでした。もう一度お試しください';

  @override
  String get invoiceDetailRejected => '請求書を却下しました';

  @override
  String get invoiceDetailRejectFailed => '請求書を却下できませんでした。もう一度お試しください';

  @override
  String get invoiceDetailRejectTitle => '請求書を却下';

  @override
  String get invoiceDetailRejectReason => '理由';

  @override
  String get invoiceDetailReject => '却下';

  @override
  String get invoiceDetailApprove => '承認';

  @override
  String get invoiceDetailUnknownVendor => '不明な取引先';

  @override
  String get invoiceDetailFieldInvoiceNumber => '請求書番号';

  @override
  String get invoiceDetailFieldPoNumber => '発注番号';

  @override
  String get invoiceDetailFieldCurrency => '通貨';

  @override
  String get invoiceDetailFieldInvoiceDate => '請求日';

  @override
  String get invoiceDetailFieldDueDate => '支払期日';

  @override
  String get invoiceDetailFieldDescription => '説明';

  @override
  String get invoiceDetailFieldGlAccount => '勘定科目';

  @override
  String get invoiceDetailFieldCreated => '作成日';

  @override
  String get invoiceDetailActivity => 'アクティビティ';

  @override
  String get invoiceDetailActivityError => 'アクティビティを読み込めませんでした';

  @override
  String get invoiceDetailFilePdfLabel => '請求書PDF。ダブルタップで全画面表示します。';

  @override
  String get invoiceDetailFileLabel => '請求書ファイル。ダブルタップで全画面表示します。';

  @override
  String get invoiceDetailTapToViewPdf => 'タップしてPDFを表示';

  @override
  String get invoiceDetailTapToViewFile => 'タップしてファイルを表示';

  @override
  String get invoiceEditTitle => '請求書を編集';

  @override
  String get invoiceEditClose => '編集フォームを閉じる';

  @override
  String get invoiceEditVendor => '取引先';

  @override
  String get invoiceEditInvoiceNumber => '請求書番号';

  @override
  String get invoiceEditAmount => '金額';

  @override
  String get invoiceEditPoNumber => '発注番号';

  @override
  String get invoiceEditGlAccount => '勘定科目';

  @override
  String get invoiceEditDescription => '説明';

  @override
  String get invoiceEditDueDate => '支払期日';

  @override
  String get invoiceEditNotSet => '未設定';

  @override
  String get invoiceEditInvalidAmount => '有効な金額を入力してください（例: 1234.56）';

  @override
  String get invoiceEditClearDueDate => '支払期日をクリア';

  @override
  String invoiceEditDueDateHint(String value) {
    return '支払期日、現在 $value。ダブルタップで変更します。';
  }

  @override
  String get warningsSectionTitle => '警告と不正フラグ';

  @override
  String get warningsPoMatchTitle => '発注照合';

  @override
  String get warningsSeverityError => 'エラー';

  @override
  String get warningsSeverityWarning => '警告';

  @override
  String get warningsSeverityInfo => '情報';

  @override
  String get warningsPoLabel => '発注';

  @override
  String warningsMatchLabel(String type) {
    return '$type照合';
  }

  @override
  String warningsVarianceLabel(String value) {
    return '$value% の差異';
  }

  @override
  String get erpStatusTitle => 'ERPステータス';

  @override
  String get erpStatusReference => 'ERP参照番号';

  @override
  String get erpStatusDocumentId => 'ドキュメントID';

  @override
  String get erpStatusError => 'エラー';

  @override
  String get erpStatusLastUpdate => '最終更新';

  @override
  String get erpStatusStatus => 'ステータス';

  @override
  String get fileViewerPdfTitle => '請求書PDF';

  @override
  String get fileViewerImageTitle => '請求書画像';

  @override
  String get fileViewerPdfError => 'PDFを読み込めませんでした';

  @override
  String get fileViewerImageError => '画像を読み込めませんでした';

  @override
  String get fileViewerRetry => '再試行';

  @override
  String get timelineNoActivity => 'まだアクティビティはありません';

  @override
  String get payTitle => '支払';

  @override
  String get payTabQueue => 'キュー';

  @override
  String get payTabRuns => '実行';

  @override
  String get paySummaryTotalPaid => '支払合計';

  @override
  String get paySummaryPending => '保留中';

  @override
  String get paySummaryInQueue => 'キュー内';

  @override
  String get paySummaryCardRebates => 'カードリベート';

  @override
  String paySummaryPaymentsSubtitle(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count件の支払',
    );
    return '$_temp0';
  }

  @override
  String get payQueueEmpty => '支払待ちの請求書はありません';

  @override
  String get payQueueError => '支払キューを読み込めませんでした';

  @override
  String get payQueueRetry => '再試行';

  @override
  String payQueueDue(String date) {
    return '期日 $date';
  }

  @override
  String get payQueueNoDueDate => '支払期日なし';

  @override
  String payQueueDiscount(String amount) {
    return '割引 $amount';
  }

  @override
  String get payQueueOverdue => '期限超過';

  @override
  String get payQueueSelected => '選択済み';

  @override
  String payMethodLabel(String invoiceNumber) {
    return '$invoiceNumber の支払方法';
  }

  @override
  String get payMethodAch => 'ACH';

  @override
  String get payMethodWire => '電信送金';

  @override
  String get payMethodCheck => '小切手';

  @override
  String get payMethodVirtualCard => 'バーチャルカード';

  @override
  String paySelectedCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count件の請求書を選択中',
    );
    return '$_temp0';
  }

  @override
  String get payClear => 'クリア';

  @override
  String get payCreateRun => '実行を作成';

  @override
  String payCreateRunFailed(String error) {
    return '実行の作成に失敗しました: $error';
  }

  @override
  String get payRunsEmpty => '支払実行はありません';

  @override
  String payRunSubtitle(int count, String date) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count件の支払',
    );
    return '$_temp0 • $date';
  }

  @override
  String get payRunCfoRequiredSuffix => ' • CFOの承認が必要';

  @override
  String payRunAnnounce(String amount, String status, String subtitle) {
    return '実行 $amount、$status、$subtitle';
  }

  @override
  String get payRunActions => '実行アクション';

  @override
  String get payRunActionExecute => '実行';

  @override
  String get payRunActionCancel => 'キャンセル';

  @override
  String get payRunCfoBlocked => 'この実行を行うにはCFOの承認が必要です。';

  @override
  String get payRunExecuteTitle => '支払実行を行いますか？';

  @override
  String payRunExecuteBody(String amount) {
    return '設定された決済プロセッサーを通じて $amount を送金します。';
  }

  @override
  String payRunExecuteFailed(String error) {
    return '実行に失敗しました: $error';
  }

  @override
  String payRunCancelFailed(String error) {
    return 'キャンセルに失敗しました: $error';
  }

  @override
  String get payRunStatusDraft => '下書き';

  @override
  String get payRunStatusCompleted => '完了';

  @override
  String get payRunStatusSubmitted => '送信済み';

  @override
  String get payRunStatusPartial => '一部';

  @override
  String get payRunStatusFailed => '失敗';

  @override
  String get payRunStatusCancelled => 'キャンセル済み';

  @override
  String get payConfirmCancel => 'キャンセル';

  @override
  String get payConfirmExecute => '実行';

  @override
  String get loginAppName => 'Better AP';

  @override
  String get loginTagline => '買掛金管理をシンプルに';

  @override
  String get loginTenant => 'テナント';

  @override
  String get loginEmail => 'メールアドレス';

  @override
  String get loginPassword => 'パスワード';

  @override
  String get loginShowPassword => 'パスワードを表示';

  @override
  String get loginHidePassword => 'パスワードを非表示';

  @override
  String get loginRequired => '必須';

  @override
  String get loginSignIn => 'サインイン';

  @override
  String get mfaTitle => '二要素認証';

  @override
  String get mfaHeading => '本人確認';

  @override
  String get mfaPromptEmail => 'メールで送信した6桁のコードを入力してください。';

  @override
  String get mfaPromptTotp => '認証アプリの6桁のコードを入力してください。';

  @override
  String get mfaEnforcedNotice =>
      '組織で二要素認証が必須となっています。今はメールコードで認証し、後でWebアプリで認証アプリの設定を完了してください。';

  @override
  String get mfaCode => 'コード';

  @override
  String get mfaCodeRequired => '必須';

  @override
  String get mfaCodeTooShort => '6桁以上入力してください';

  @override
  String get mfaVerify => '確認';

  @override
  String get mfaSending => '送信中…';

  @override
  String get mfaResendEmailCode => 'メールコードを再送信';

  @override
  String get mfaSendEmailCode => 'メールコードを送信';

  @override
  String get mfaUseEmailInstead => '代わりにメールコードを使用';

  @override
  String get mfaUseAuthenticatorInstead => '代わりに認証アプリを使用';

  @override
  String get mfaEmailedAnnounce => 'サインインコードをメールで送信しました。';

  @override
  String get adminUsersTitle => 'ユーザー管理';

  @override
  String get adminUsersSearchHint => '名前またはメールで検索';

  @override
  String get adminUsersEmpty => 'ユーザーが見つかりません';

  @override
  String get adminUsersLoadError => 'ユーザーを読み込めませんでした';

  @override
  String get adminUsersEditRoles => 'ロールを編集';

  @override
  String get adminUsersNoRoles => 'ロールなし';

  @override
  String get adminUsersDeactivate => 'ユーザーを無効化';

  @override
  String get adminUsersActivate => 'ユーザーを有効化';

  @override
  String get adminUsersCannotDeactivateSelf => '自分のアカウントは無効化できません';

  @override
  String get adminUsersDeactivateHint => 'サインアウトさせ、サインインをブロックします';

  @override
  String get adminUsersActivateHint => 'サインインアクセスを復元します';

  @override
  String get adminUsersRoleActive => '有効';

  @override
  String get adminUsersRoleInactive => '無効';

  @override
  String get adminUsersInactiveBadge => '無効';

  @override
  String adminUsersRolesUpdated(String name) {
    return '$name のロールを更新しました';
  }

  @override
  String adminUsersRolesUpdateFailed(String error) {
    return 'ロールの更新に失敗しました: $error';
  }

  @override
  String adminUsersActivated(String name) {
    return '$name を有効化しました';
  }

  @override
  String adminUsersDeactivated(String name) {
    return '$name を無効化しました';
  }

  @override
  String adminUsersUpdateFailed(String name, String error) {
    return '$name の更新に失敗しました: $error';
  }

  @override
  String get orgSettingsTitle => '組織設定';

  @override
  String get orgSettingsNoSettings => '設定がありません';

  @override
  String get orgSettingsLoadError => '設定を読み込めませんでした';

  @override
  String get orgSettingsSectionCompany => '会社';

  @override
  String get orgSettingsSectionInvoiceDefaults => '請求書のデフォルト';

  @override
  String get orgSettingsName => '組織名';

  @override
  String get orgSettingsAddress => '住所';

  @override
  String get orgSettingsPhone => '電話番号';

  @override
  String get orgSettingsWebsite => 'ウェブサイト';

  @override
  String get orgSettingsTaxId => '税務ID';

  @override
  String get orgSettingsCurrency => 'デフォルト通貨';

  @override
  String get orgSettingsPaymentTerms => '支払条件';

  @override
  String get orgSettingsNumberPrefix => '請求書番号のプレフィックス';

  @override
  String get orgSettingsGlAccount => 'デフォルトの総勘定元帳アカウント';

  @override
  String get orgSettingsCostCenter => 'デフォルトのコストセンター';

  @override
  String get orgSettingsSave => '変更を保存';

  @override
  String get orgSettingsSaving => '保存中…';

  @override
  String orgSettingsFieldRequired(String label) {
    return '$label は必須です';
  }

  @override
  String get orgSettingsSaved => '組織設定を保存しました';

  @override
  String orgSettingsSaveFailed(String error) {
    return '保存に失敗しました: $error';
  }

  @override
  String get workflowsTitle => 'ワークフロー';

  @override
  String get workflowsEmpty => 'ワークフローが見つかりません';

  @override
  String get workflowsLoadError => 'ワークフローを読み込めませんでした';

  @override
  String get workflowsStatusActive => '有効';

  @override
  String get workflowsStatusInactive => '無効';

  @override
  String get workflowsDefault => 'デフォルト';

  @override
  String workflowsStepCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$countステップ',
    );
    return '$_temp0';
  }

  @override
  String get workflowDetailFallbackTitle => 'ワークフロー';

  @override
  String get workflowDetailLoadError => 'ワークフローを読み込めませんでした';

  @override
  String get workflowDetailNoSteps => 'このワークフローにはステップがありません。';

  @override
  String get workflowDetailDefaultWorkflow => 'デフォルトのワークフロー';

  @override
  String workflowDetailStepNumber(int number) {
    return 'ステップ $number';
  }

  @override
  String get workflowDetailStepEnabled => '有効';

  @override
  String get workflowDetailStepDisabled => '無効';

  @override
  String workflowDetailApproverCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '承認者$count人',
    );
    return '$_temp0';
  }

  @override
  String workflowDetailDelaySummary(String hours) {
    return '遅延 $hours 時間';
  }

  @override
  String workflowDetailConditionSummary(String field) {
    return '$field で';
  }
}
