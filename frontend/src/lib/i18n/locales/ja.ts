import type { Messages } from '../messages';

// Japanese catalogue. Lazy-imported by the runtime (catalogues.ts) so it
// splits into its own chunk. `satisfies Messages` makes a missing/extra
// key a compile error; messages_parity.test.ts re-checks at runtime.
//
// Japanese has no grammatical plural — `Intl.PluralRules('ja')` only ever
// returns `other`, so each ICU plural block keeps a single `other` arm
// (an `one` arm would be dead but is harmless; we omit it).
export const messages = {
	// Primary navigation — top-level links
	'nav.dashboard': 'ダッシュボード',
	'nav.invoices': '請求書',
	'nav.payments': '支払い',
	'nav.vendors': '取引先',
	'nav.exceptions': '例外',

	// Primary navigation — group labels
	'nav.group.procurement': '調達',
	'nav.group.billing': '請求',
	'nav.group.insights': '分析',
	'nav.group.settings': '設定',

	// Primary navigation — group children
	'nav.purchaseOrders': '発注書',
	'nav.goodsReceipts': '入荷',
	'nav.requisitions': '購買依頼',
	'nav.intake': '受信',
	'nav.catalogs': 'カタログ',
	'nav.budgets': '予算',
	'nav.contracts': '契約',
	'nav.expenses': '経費',
	'nav.creditMemos': '貸方票',
	'nav.discounts': '割引',
	'nav.recurring': '定期',
	'nav.statements': '明細書',
	'nav.positivePay': 'ポジティブペイ',
	'nav.platformBilling': 'サブスクリプション',
	'nav.aiAssistant': 'AIアシスタント',
	'nav.cashFlow': 'キャッシュフロー',
	'nav.taxReporting': '1099報告',
	'nav.organization': '組織',
	'nav.users': 'ユーザー',
	'nav.roles': 'ロール',
	'nav.auditTrail': '監査証跡',
	'nav.workflows': 'ワークフロー',

	// App shell / sidebar
	'shell.appName': 'Account Payables',
	'shell.skipToMain': 'メインコンテンツへスキップ',
	'shell.primaryNav': 'メインナビゲーション',
	'shell.sectionNav': 'セクション：{group}',
	'shell.profileMenu': 'プロフィールとアカウントメニュー',
	'shell.profile': 'プロフィール',
	'shell.profileAndSecurity': 'プロフィールとセキュリティ',
	'shell.logOut': 'ログアウト',
	'shell.expandSidebar': 'サイドバーを展開',
	'shell.collapseSidebar': 'サイドバーを折りたたむ',
	'shell.collapse': '折りたたむ',

	// Common buttons / states
	'common.save': '保存',
	'common.saving': '保存中…',
	'common.cancel': 'キャンセル',
	'common.loading': '読み込み中…',

	// Profile → Language picker
	'profile.language.heading': '言語',
	'profile.language.hint':
		'アプリ全体で使用する言語を選択してください。選択内容はこの端末に保存されます。',
	'profile.language.label': '表示言語',

	// Common shared across list/detail surfaces
	'common.all': 'すべて',
	'common.search': '検索',
	'common.clear': 'クリア',
	'common.apply': '適用',

	// Dashboard
	'dashboard.title': 'ダッシュボード',
	'dashboard.kpi.invoices': '請求書',
	'dashboard.kpi.totalAmount': '合計金額',
	'dashboard.kpi.paid': '支払済み',
	'dashboard.kpi.pending': '保留中',
	'dashboard.kpi.touchlessRate': '自動化率',
	'dashboard.kpi.exceptions': '例外',
	'dashboard.kpi.staleApprovals': '滞留中の承認',
	'dashboard.kpi.rebatesEarned': '獲得リベート',
	'dashboard.chart.pipeline': '請求書パイプライン',
	'dashboard.chart.topVendors': '支出上位の取引先',
	'dashboard.chart.aging': '請求書の経過日数',
	'dashboard.chart.upcoming': '予定および期限超過',
	'dashboard.chart.monthlyVolume': '月次ボリューム',
	'dashboard.empty.vendors': '請求書データはまだありません。',
	'dashboard.empty.aging': '期限のある未処理の請求書はありません。',
	'dashboard.empty.upcoming': '今週予定されている支払いはありません。',
	'dashboard.aging.current': '期限内',
	'dashboard.aging.days30': '1〜30日',
	'dashboard.aging.days60': '31〜60日',
	'dashboard.aging.days90': '61〜90日',
	'dashboard.aging.days90plus': '90日以上',
	'dashboard.overdue': '期限超過',

	// Invoices list
	'invoices.title': '請求書',
	'invoices.action.bulkRecode': '勘定科目を一括再コード',
	'invoices.action.upload': '+ 請求書をアップロード',
	'invoices.action.uploading': 'アップロード中…',
	'invoices.action.uploadingProgress': '{total}件中{done}件をアップロード中…',
	'invoices.search.placeholder': '請求書を検索…',
	'invoices.search.aria': '請求書を検索',
	'invoices.search.advanced': '詳細検索',
	'invoices.bulk.selected': '{n, plural, other {#件を選択中}}',
	'invoices.bulk.delete': '削除',
	'invoices.bulk.confirmDelete': '削除を確認',
	'invoices.bulk.changeStatus': 'ステータスを変更',
	'invoices.bulk.cannotDelete': 'システム管理ステータスの請求書は削除できません',
	'invoices.bulk.noTransitions': '選択した請求書に共通するステータス遷移がありません',
	'invoices.bulk.newStatusAria': '選択した請求書の新しいステータス',
	'invoices.col.invoiceNumber': '請求書番号',
	'invoices.col.vendor': '取引先',
	'invoices.col.description': '説明',
	'invoices.col.poNumber': '発注番号',
	'invoices.col.amount': '金額',
	'invoices.col.dueDate': '支払期限',
	'invoices.col.status': 'ステータス',
	'invoices.selectAllAria': 'すべての請求書を選択',
	'invoices.empty': 'フィルターに一致する請求書はありません。',
	'invoices.row.delete': '削除',
	'invoices.row.confirm': '確認',
	'invoices.loadMore': 'さらに読み込む（{total}件中{shown}件）',
	'invoices.showingAll': '{total, plural, other {#件すべての請求書を表示中}}',
} satisfies Messages;
