import type { Messages } from '../messages';

// Spanish catalogue. Lazy-imported by the runtime (catalogues.ts) so it
// splits into its own chunk. `satisfies Messages` makes a missing/extra
// key a compile error; messages_parity.test.ts re-checks at runtime.
export const messages = {
	// Primary navigation — top-level links
	'nav.dashboard': 'Panel',
	'nav.invoices': 'Facturas',
	'nav.payments': 'Pagos',
	'nav.vendors': 'Proveedores',
	'nav.exceptions': 'Excepciones',

	// Primary navigation — group labels
	'nav.group.procurement': 'Compras',
	'nav.group.billing': 'Facturación',
	'nav.group.insights': 'Análisis',
	'nav.group.settings': 'Ajustes',

	// Primary navigation — group children
	'nav.purchaseOrders': 'Órdenes de compra',
	'nav.goodsReceipts': 'Recepciones de mercancía',
	'nav.requisitions': 'Solicitudes de compra',
	'nav.intake': 'Recepción',
	'nav.catalogs': 'Catálogos',
	'nav.budgets': 'Presupuestos',
	'nav.contracts': 'Contratos',
	'nav.expenses': 'Gastos',
	'nav.creditMemos': 'Notas de crédito',
	'nav.discounts': 'Descuentos',
	'nav.recurring': 'Recurrentes',
	'nav.statements': 'Estados de cuenta',
	'nav.positivePay': 'Positive Pay',
	'nav.platformBilling': 'Suscripción',
	'nav.aiAssistant': 'Asistente de IA',
	'nav.cashFlow': 'Flujo de caja',
	'nav.taxReporting': 'Informe 1099',
	'nav.organization': 'Organización',
	'nav.users': 'Usuarios',
	'nav.roles': 'Roles',
	'nav.auditTrail': 'Registro de auditoría',
	'nav.workflows': 'Flujos de trabajo',

	// App shell / sidebar
	'shell.appName': 'Account Payables',
	'shell.skipToMain': 'Saltar al contenido principal',
	'shell.primaryNav': 'Navegación principal',
	'shell.sectionNav': 'Secciones: {group}',
	'shell.profileMenu': 'Menú de perfil y cuenta',
	'shell.profile': 'Perfil',
	'shell.profileAndSecurity': 'Perfil y seguridad',
	'shell.logOut': 'Cerrar sesión',
	'shell.expandSidebar': 'Expandir barra lateral',
	'shell.collapseSidebar': 'Contraer barra lateral',
	'shell.collapse': 'Contraer',

	// Common buttons / states
	'common.save': 'Guardar',
	'common.saving': 'Guardando…',
	'common.cancel': 'Cancelar',
	'common.loading': 'Cargando…',

	// Profile → Language picker
	'profile.language.heading': 'Idioma',
	'profile.language.hint':
		'Elija el idioma utilizado en toda la aplicación. Su elección se guarda en este dispositivo.',
	'profile.language.label': 'Idioma de visualización',

	// Common shared across list/detail surfaces
	'common.all': 'Todas',
	'common.search': 'Buscar',
	'common.clear': 'Limpiar',
	'common.apply': 'Aplicar',

	// Dashboard
	'dashboard.title': 'Panel',
	'dashboard.kpi.invoices': 'Facturas',
	'dashboard.kpi.totalAmount': 'Importe total',
	'dashboard.kpi.paid': 'Pagadas',
	'dashboard.kpi.pending': 'Pendientes',
	'dashboard.kpi.touchlessRate': 'Tasa de automatización',
	'dashboard.kpi.exceptions': 'Excepciones',
	'dashboard.kpi.staleApprovals': 'Aprobaciones atrasadas',
	'dashboard.kpi.rebatesEarned': 'Reembolsos obtenidos',
	'dashboard.chart.pipeline': 'Flujo de facturas',
	'dashboard.chart.topVendors': 'Principales proveedores por gasto',
	'dashboard.chart.aging': 'Antigüedad de facturas',
	'dashboard.chart.upcoming': 'Próximas y vencidas',
	'dashboard.chart.monthlyVolume': 'Volumen mensual',
	'dashboard.empty.vendors': 'Aún no hay datos de facturas.',
	'dashboard.empty.aging': 'No hay facturas abiertas con fecha de vencimiento.',
	'dashboard.empty.upcoming': 'No hay pagos previstos esta semana.',
	'dashboard.aging.current': 'Al día',
	'dashboard.aging.days30': '1 a 30 días',
	'dashboard.aging.days60': '31 a 60 días',
	'dashboard.aging.days90': '61 a 90 días',
	'dashboard.aging.days90plus': 'más de 90 días',
	'dashboard.overdue': 'Vencida',

	// Invoices list
	'invoices.title': 'Facturas',
	'invoices.action.bulkRecode': 'Recodificar cuentas',
	'invoices.action.upload': '+ Subir facturas',
	'invoices.action.uploading': 'Subiendo…',
	'invoices.action.uploadingProgress': 'Subiendo {done} de {total}…',
	'invoices.search.placeholder': 'Buscar facturas…',
	'invoices.search.aria': 'Buscar facturas',
	'invoices.search.advanced': 'Búsqueda avanzada',
	'invoices.bulk.selected': '{n, plural, one {# seleccionada} other {# seleccionadas}}',
	'invoices.bulk.delete': 'Eliminar',
	'invoices.bulk.confirmDelete': 'Confirmar eliminación',
	'invoices.bulk.changeStatus': 'Cambiar estado',
	'invoices.bulk.cannotDelete': 'No se pueden eliminar facturas en estados gestionados por el sistema',
	'invoices.bulk.noTransitions': 'No hay transiciones de estado comunes para las facturas seleccionadas',
	'invoices.bulk.newStatusAria': 'Nuevo estado para las facturas seleccionadas',
	'invoices.col.invoiceNumber': 'N.º de factura',
	'invoices.col.vendor': 'Proveedor',
	'invoices.col.description': 'Descripción',
	'invoices.col.poNumber': 'N.º de OC',
	'invoices.col.amount': 'Importe',
	'invoices.col.dueDate': 'Vencimiento',
	'invoices.col.status': 'Estado',
	'invoices.selectAllAria': 'Seleccionar todas las facturas',
	'invoices.empty': 'Ninguna factura coincide con sus filtros.',
	'invoices.row.delete': 'Eliminar',
	'invoices.row.confirm': 'Confirmar',
	'invoices.loadMore': 'Cargar más ({shown} de {total})',
	'invoices.showingAll': '{total, plural, one {Mostrando # factura} other {Mostrando todas las # facturas}}',
} satisfies Messages;
