import type { Messages } from '../messages';

// Brazilian Portuguese catalogue. Lazy-imported by the runtime
// (catalogues.ts) so it splits into its own chunk. `satisfies Messages`
// makes a missing/extra key a compile error; messages_parity.test.ts
// re-checks at runtime.
export const messages = {
	// Primary navigation — top-level links
	'nav.dashboard': 'Painel',
	'nav.invoices': 'Faturas',
	'nav.payments': 'Pagamentos',
	'nav.vendors': 'Fornecedores',
	'nav.exceptions': 'Exceções',

	// Primary navigation — group labels
	'nav.group.procurement': 'Compras',
	'nav.group.billing': 'Faturamento',
	'nav.group.insights': 'Análises',
	'nav.group.settings': 'Configurações',

	// Primary navigation — group children
	'nav.purchaseOrders': 'Pedidos de compra',
	'nav.goodsReceipts': 'Recebimentos de mercadorias',
	'nav.requisitions': 'Requisições',
	'nav.intake': 'Entrada',
	'nav.catalogs': 'Catálogos',
	'nav.budgets': 'Orçamentos',
	'nav.contracts': 'Contratos',
	'nav.expenses': 'Despesas',
	'nav.creditMemos': 'Notas de crédito',
	'nav.discounts': 'Descontos',
	'nav.recurring': 'Recorrentes',
	'nav.statements': 'Extratos',
	'nav.positivePay': 'Positive Pay',
	'nav.platformBilling': 'Assinatura',
	'nav.aiAssistant': 'Assistente de IA',
	'nav.cashFlow': 'Fluxo de caixa',
	'nav.taxReporting': 'Relatório 1099',
	'nav.organization': 'Organização',
	'nav.users': 'Usuários',
	'nav.roles': 'Funções',
	'nav.auditTrail': 'Trilha de auditoria',
	'nav.workflows': 'Fluxos de trabalho',

	// App shell / sidebar
	'shell.appName': 'Account Payables',
	'shell.skipToMain': 'Pular para o conteúdo principal',
	'shell.primaryNav': 'Navegação principal',
	'shell.sectionNav': 'Seções: {group}',
	'shell.profileMenu': 'Menu de perfil e conta',
	'shell.profile': 'Perfil',
	'shell.profileAndSecurity': 'Perfil e segurança',
	'shell.logOut': 'Sair',
	'shell.expandSidebar': 'Expandir barra lateral',
	'shell.collapseSidebar': 'Recolher barra lateral',
	'shell.collapse': 'Recolher',

	// Common buttons / states
	'common.save': 'Salvar',
	'common.saving': 'Salvando…',
	'common.cancel': 'Cancelar',
	'common.loading': 'Carregando…',

	// Profile → Language picker
	'profile.language.heading': 'Idioma',
	'profile.language.hint':
		'Escolha o idioma usado em todo o aplicativo. Sua escolha é salva neste dispositivo.',
	'profile.language.label': 'Idioma de exibição',

	// Common shared across list/detail surfaces
	'common.all': 'Todas',
	'common.search': 'Pesquisar',
	'common.clear': 'Limpar',
	'common.apply': 'Aplicar',

	// Dashboard
	'dashboard.title': 'Painel',
	'dashboard.kpi.invoices': 'Faturas',
	'dashboard.kpi.totalAmount': 'Valor total',
	'dashboard.kpi.paid': 'Pagas',
	'dashboard.kpi.pending': 'Pendentes',
	'dashboard.kpi.touchlessRate': 'Taxa de automação',
	'dashboard.kpi.exceptions': 'Exceções',
	'dashboard.kpi.staleApprovals': 'Aprovações atrasadas',
	'dashboard.kpi.rebatesEarned': 'Reembolsos obtidos',
	'dashboard.chart.pipeline': 'Pipeline de faturas',
	'dashboard.chart.topVendors': 'Principais fornecedores por gasto',
	'dashboard.chart.aging': 'Idade das faturas',
	'dashboard.chart.upcoming': 'A vencer e vencidas',
	'dashboard.chart.monthlyVolume': 'Volume mensal',
	'dashboard.empty.vendors': 'Ainda não há dados de faturas.',
	'dashboard.empty.aging': 'Nenhuma fatura aberta com data de vencimento.',
	'dashboard.empty.upcoming': 'Nenhum pagamento previsto esta semana.',
	'dashboard.aging.current': 'Em dia',
	'dashboard.aging.days30': '1 a 30 dias',
	'dashboard.aging.days60': '31 a 60 dias',
	'dashboard.aging.days90': '61 a 90 dias',
	'dashboard.aging.days90plus': 'mais de 90 dias',
	'dashboard.overdue': 'Vencida',

	// Invoices list
	'invoices.title': 'Faturas',
	'invoices.action.bulkRecode': 'Recodificar contas',
	'invoices.action.upload': '+ Enviar faturas',
	'invoices.action.uploading': 'Enviando…',
	'invoices.action.uploadingProgress': 'Enviando {done} de {total}…',
	'invoices.search.placeholder': 'Pesquisar faturas…',
	'invoices.search.aria': 'Pesquisar faturas',
	'invoices.search.advanced': 'Pesquisa avançada',
	'invoices.bulk.selected': '{n, plural, one {# selecionada} other {# selecionadas}}',
	'invoices.bulk.delete': 'Excluir',
	'invoices.bulk.confirmDelete': 'Confirmar exclusão',
	'invoices.bulk.changeStatus': 'Alterar status',
	'invoices.bulk.cannotDelete': 'Não é possível excluir faturas em status gerenciados pelo sistema',
	'invoices.bulk.noTransitions': 'Nenhuma transição de status comum para as faturas selecionadas',
	'invoices.bulk.newStatusAria': 'Novo status para as faturas selecionadas',
	'invoices.col.invoiceNumber': 'N.º da fatura',
	'invoices.col.vendor': 'Fornecedor',
	'invoices.col.description': 'Descrição',
	'invoices.col.poNumber': 'N.º do pedido',
	'invoices.col.amount': 'Valor',
	'invoices.col.dueDate': 'Vencimento',
	'invoices.col.status': 'Status',
	'invoices.selectAllAria': 'Selecionar todas as faturas',
	'invoices.empty': 'Nenhuma fatura corresponde aos seus filtros.',
	'invoices.row.delete': 'Excluir',
	'invoices.row.confirm': 'Confirmar',
	'invoices.loadMore': 'Carregar mais ({shown} de {total})',
	'invoices.showingAll': '{total, plural, one {Exibindo # fatura} other {Exibindo todas as # faturas}}',
} satisfies Messages;
