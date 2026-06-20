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
  String get commonClose => 'Cerrar';

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

  @override
  String get notificationsTitle => 'Notificaciones';

  @override
  String get notificationsMarkAllRead => 'Marcar todo como leído';

  @override
  String get notificationsMarkAllReadLabel =>
      'Marcar todas las notificaciones como leídas';

  @override
  String get notificationsFilterUnread => 'No leídas';

  @override
  String get notificationsAllMarkedRead =>
      'Todas las notificaciones marcadas como leídas';

  @override
  String get notificationsCouldNotMarkAll =>
      'No se pudo marcar todo como leído';

  @override
  String get notificationsEmptyUnread => 'No hay notificaciones sin leer';

  @override
  String get notificationsEmpty => 'No hay notificaciones';

  @override
  String get notificationsCaughtUp => 'Estás al día';

  @override
  String get notificationsNothingYet => 'Aún no hay nada aquí';

  @override
  String get notificationsLoadError =>
      'No se pudieron cargar las notificaciones';

  @override
  String get vendorsTitle => 'Proveedores';

  @override
  String get vendorsSyncErp => 'Sincronizar desde ERP';

  @override
  String get vendorsSyncErpLabel => 'Sincronizar proveedores desde ERP';

  @override
  String get vendorsSearchHint => 'Buscar proveedores…';

  @override
  String get vendorsFilterUnverified => 'Sin verificar';

  @override
  String get vendorsFilterActive => 'Activos';

  @override
  String get vendorsFilterInactive => 'Inactivos';

  @override
  String get vendorsFilterRejected => 'Rechazados';

  @override
  String get vendorsEmpty => 'No se encontraron proveedores';

  @override
  String get vendorsLoadError => 'No se pudieron cargar los proveedores';

  @override
  String get vendorActionVerify => 'Verificar';

  @override
  String get vendorActionReject => 'Rechazar';

  @override
  String get vendorUnverifiedLabel => 'Proveedor sin verificar';

  @override
  String get vendorVerifyHint => 'Habilitar para pago';

  @override
  String get vendorRejectHint => 'Marcar como no válido / duplicado';

  @override
  String get vendorVerified => 'Proveedor verificado';

  @override
  String get vendorRejected => 'Proveedor rechazado';

  @override
  String get vendorActionFailed => 'Acción fallida';

  @override
  String vendorSyncFailed(String error) {
    return 'Error de sincronización con ERP: $error';
  }

  @override
  String get exceptionsTitle => 'Excepciones';

  @override
  String get exceptionsFilterOpen => 'Abiertas';

  @override
  String get exceptionsFilterEscalated => 'Escaladas';

  @override
  String get exceptionsFilterResolved => 'Resueltas';

  @override
  String get exceptionsFilterDismissed => 'Descartadas';

  @override
  String get exceptionsEmpty => 'No hay excepciones';

  @override
  String get exceptionsQueueClear => 'La cola de excepciones está vacía';

  @override
  String get exceptionActionResolve => 'Resolver';

  @override
  String get exceptionActionEscalate => 'Escalar';

  @override
  String get exceptionActionDismiss => 'Descartar';

  @override
  String get exceptionResolved => 'Excepción resuelta';

  @override
  String get exceptionEscalated => 'Excepción escalada';

  @override
  String get exceptionDismissed => 'Excepción descartada';

  @override
  String get exceptionActionFailed => 'Acción fallida';

  @override
  String get paymentsTitle => 'Pagos';

  @override
  String get paymentsEmpty => 'No hay pagos';

  @override
  String paymentsErrorPrefix(String error) {
    return 'Error: $error';
  }

  @override
  String get paymentStatusPending => 'Pendiente';

  @override
  String get paymentStatusProcessing => 'En proceso';

  @override
  String get paymentStatusCompleted => 'Completado';

  @override
  String get paymentStatusFailed => 'Fallido';

  @override
  String get paymentStatusCancelled => 'Cancelado';

  @override
  String get approvalsTitle => 'Aprobaciones pendientes';

  @override
  String get approvalsAllCaughtUp => '¡Todo al día!';

  @override
  String get approvalsNoneWaiting => 'No hay facturas esperando aprobación';

  @override
  String approvalsPendingCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count facturas pendientes',
      one: '$count factura pendiente',
    );
    return '$_temp0';
  }

  @override
  String get approvalActionApprove => 'Aprobar';

  @override
  String get approvalActionReject => 'Rechazar';

  @override
  String get approvalApproved => 'Factura aprobada';

  @override
  String get captureTitle => 'Capturar factura';

  @override
  String get captureChange => 'Cambiar';

  @override
  String get captureUpload => 'Subir';

  @override
  String get captureUploading => 'Subiendo…';

  @override
  String get captureEmptyPrompt =>
      'Toma una foto, elige de la galería o selecciona un archivo';

  @override
  String get captureCamera => 'Cámara';

  @override
  String get captureGallery => 'Galería';

  @override
  String get captureChooseFile => 'Elegir archivo';

  @override
  String get captureSupportedFormats => 'Admite PDF, PNG, JPG y TIFF';

  @override
  String get captureUploadSuccess => 'Factura subida correctamente';

  @override
  String captureUploadFailedStatus(int status, String message) {
    return 'Error al subir ($status): $message';
  }

  @override
  String captureUploadFailed(String error) {
    return 'Error al subir: $error';
  }

  @override
  String captureSelectedDocument(String name) {
    return 'Documento seleccionado: $name';
  }

  @override
  String get capturePdfReady => 'Documento PDF listo para subir';

  @override
  String get advSearchTitle => 'Búsqueda avanzada';

  @override
  String get advSearchClose => 'Cerrar búsqueda avanzada';

  @override
  String get advSearchVendor => 'Proveedor';

  @override
  String get advSearchPoNumber => 'N.º de pedido';

  @override
  String get advSearchMinAmount => 'Importe mínimo';

  @override
  String get advSearchMaxAmount => 'Importe máximo';

  @override
  String get advSearchDueFrom => 'Vence desde';

  @override
  String get advSearchDueTo => 'Vence hasta';

  @override
  String get advSearchAny => 'Cualquiera';

  @override
  String get advSearchInvalidAmount =>
      'Introduce un importe válido (p. ej. 1000)';

  @override
  String get advSearchMinMaxError => 'El mínimo no debe superar el máximo';

  @override
  String advSearchClearField(String label) {
    return 'Borrar $label';
  }

  @override
  String advSearchDateFieldHint(String label, String value) {
    return '$label, actualmente $value. Toca dos veces para cambiar.';
  }
}
