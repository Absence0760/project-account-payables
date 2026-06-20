// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Spanish Castilian (`es`).
class AppLocalizationsEs extends AppLocalizations {
  AppLocalizationsEs([String locale = 'es']) : super(locale);

  @override
  String get navDashboard => 'Panel';

  @override
  String get navInvoices => 'Facturas';

  @override
  String get navContracts => 'Contratos';

  @override
  String get navApprovals => 'Aprobaciones';

  @override
  String get navExceptions => 'Excepciones';

  @override
  String get navVendors => 'Proveedores';

  @override
  String get navPay => 'Pagar';

  @override
  String get navPayments => 'Pagos';

  @override
  String get navSettings => 'Ajustes';

  @override
  String get shellAppName => 'Account Payables';

  @override
  String get commonSave => 'Guardar';

  @override
  String get commonSaving => 'Guardando…';

  @override
  String get commonCancel => 'Cancelar';

  @override
  String get commonLoading => 'Cargando…';

  @override
  String get commonRetry => 'Reintentar';

  @override
  String get commonAll => 'Todas';

  @override
  String get commonSearch => 'Buscar';

  @override
  String get commonClear => 'Limpiar';

  @override
  String get commonApply => 'Aplicar';

  @override
  String get settingsTitle => 'Ajustes';

  @override
  String get settingsTenant => 'Organización';

  @override
  String get settingsTenantNotSet => 'Sin definir';

  @override
  String get settingsApiServer => 'Servidor API';

  @override
  String get settingsBiometricUnlock => 'Desbloqueo biométrico';

  @override
  String get settingsBiometricHint => 'Usar huella o rostro para desbloquear';

  @override
  String get settingsSignOut => 'Cerrar sesión';

  @override
  String get settingsLanguage => 'Idioma';

  @override
  String get settingsLanguageHint =>
      'Elija el idioma utilizado en toda la aplicación. Su elección se guarda en este dispositivo.';

  @override
  String get settingsLanguageSystem => 'Predeterminado del sistema';

  @override
  String get dashboardTitle => 'Panel';

  @override
  String get dashboardTotalInvoices => 'Total de facturas';

  @override
  String get dashboardUpcoming => 'Próximas';

  @override
  String get dashboardForReview => 'Para revisar';

  @override
  String get dashboardApproved => 'Aprobadas';

  @override
  String get dashboardAging => 'Antigüedad de facturas';

  @override
  String get dashboardTopVendors => 'Principales proveedores';

  @override
  String get dashboardAgingCurrent => 'Al día';

  @override
  String get dashboardAgingDays30 => '30 días';

  @override
  String get dashboardAgingDays60 => '60 días';

  @override
  String get dashboardAgingDays90plus => '90+';

  @override
  String get dashboardCachedBanner =>
      'Datos en caché — no se pudo conectar al servidor';

  @override
  String dashboardErrorPrefix(String error) {
    return 'Error: $error';
  }

  @override
  String dashboardInvoiceCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count facturas',
      one: '$count factura',
    );
    return '$_temp0';
  }

  @override
  String get invoicesTitle => 'Facturas';

  @override
  String get invoicesSearchHint => 'Buscar facturas…';

  @override
  String get invoicesSearchAria => 'Buscar facturas';

  @override
  String get invoicesAdvancedSearch => 'Búsqueda avanzada';

  @override
  String get invoicesAdvancedSearchActive =>
      'Búsqueda avanzada, filtros activos';

  @override
  String get invoicesCaptureInvoice => 'Capturar factura';

  @override
  String get invoicesCaptureInvoiceLabel => 'Capturar factura';

  @override
  String get invoicesEmpty => 'No se encontraron facturas';

  @override
  String get invoicesFilterAll => 'Todas';

  @override
  String get invoicesFilterNew => 'Nuevas';

  @override
  String get invoicesFilterPending => 'Pendientes';

  @override
  String get invoicesFilterReview => 'Revisión';

  @override
  String get invoicesFilterApproved => 'Aprobadas';

  @override
  String get invoicesFilterRejected => 'Rechazadas';

  @override
  String get invoicesFilterPaid => 'Pagadas';

  @override
  String get invoicesColInvoiceNumber => 'N.º de factura';

  @override
  String get invoicesColVendor => 'Proveedor';

  @override
  String get invoicesColAmount => 'Importe';

  @override
  String get invoicesColDueDate => 'Vencimiento';

  @override
  String get invoicesColStatus => 'Estado';
}
