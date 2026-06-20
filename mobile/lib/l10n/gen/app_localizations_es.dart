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

  @override
  String get invoiceDetailTitle => 'Detalle de la factura';

  @override
  String get invoiceDetailEdit => 'Editar';

  @override
  String get invoiceDetailEditLabel => 'Editar factura';

  @override
  String get invoiceDetailRetry => 'Reintentar';

  @override
  String invoiceDetailErrorPrefix(String error) {
    return 'Error: $error';
  }

  @override
  String get invoiceDetailNoChanges => 'No hay cambios que guardar';

  @override
  String get invoiceDetailUpdated => 'Factura actualizada';

  @override
  String get invoiceDetailUpdateFailed =>
      'No se pudieron guardar los cambios — inténtalo de nuevo';

  @override
  String get invoiceDetailApproved => 'Factura aprobada';

  @override
  String get invoiceDetailApproveFailed =>
      'No se pudo aprobar la factura — inténtalo de nuevo';

  @override
  String get invoiceDetailRejected => 'Factura rechazada';

  @override
  String get invoiceDetailRejectFailed =>
      'No se pudo rechazar la factura — inténtalo de nuevo';

  @override
  String get invoiceDetailRejectTitle => 'Rechazar factura';

  @override
  String get invoiceDetailRejectReason => 'Motivo';

  @override
  String get invoiceDetailReject => 'Rechazar';

  @override
  String get invoiceDetailApprove => 'Aprobar';

  @override
  String get invoiceDetailUnknownVendor => 'Proveedor desconocido';

  @override
  String get invoiceDetailFieldInvoiceNumber => 'N.º de factura';

  @override
  String get invoiceDetailFieldPoNumber => 'N.º de pedido';

  @override
  String get invoiceDetailFieldCurrency => 'Moneda';

  @override
  String get invoiceDetailFieldInvoiceDate => 'Fecha de factura';

  @override
  String get invoiceDetailFieldDueDate => 'Fecha de vencimiento';

  @override
  String get invoiceDetailFieldDescription => 'Descripción';

  @override
  String get invoiceDetailFieldGlAccount => 'Cuenta contable';

  @override
  String get invoiceDetailFieldCreated => 'Creada';

  @override
  String get invoiceDetailActivity => 'Actividad';

  @override
  String get invoiceDetailActivityError => 'No se pudo cargar la actividad';

  @override
  String get invoiceDetailFilePdfLabel =>
      'PDF de la factura. Toca dos veces para ver a pantalla completa.';

  @override
  String get invoiceDetailFileLabel =>
      'Archivo de la factura. Toca dos veces para ver a pantalla completa.';

  @override
  String get invoiceDetailTapToViewPdf => 'Toca para ver el PDF';

  @override
  String get invoiceDetailTapToViewFile => 'Toca para ver el archivo';

  @override
  String get invoiceEditTitle => 'Editar factura';

  @override
  String get invoiceEditClose => 'Cerrar el formulario de edición';

  @override
  String get invoiceEditVendor => 'Proveedor';

  @override
  String get invoiceEditInvoiceNumber => 'N.º de factura';

  @override
  String get invoiceEditAmount => 'Importe';

  @override
  String get invoiceEditPoNumber => 'N.º de pedido';

  @override
  String get invoiceEditGlAccount => 'Cuenta contable';

  @override
  String get invoiceEditDescription => 'Descripción';

  @override
  String get invoiceEditDueDate => 'Fecha de vencimiento';

  @override
  String get invoiceEditNotSet => 'Sin definir';

  @override
  String get invoiceEditInvalidAmount =>
      'Introduce un importe válido (p. ej. 1234,56)';

  @override
  String get invoiceEditClearDueDate => 'Borrar fecha de vencimiento';

  @override
  String invoiceEditDueDateHint(String value) {
    return 'Fecha de vencimiento, actualmente $value. Toca dos veces para cambiar.';
  }

  @override
  String get warningsSectionTitle => 'Avisos y alertas de fraude';

  @override
  String get warningsPoMatchTitle => 'Conciliación de pedido';

  @override
  String get warningsSeverityError => 'Error';

  @override
  String get warningsSeverityWarning => 'Aviso';

  @override
  String get warningsSeverityInfo => 'Información';

  @override
  String get warningsPoLabel => 'Pedido';

  @override
  String warningsMatchLabel(String type) {
    return 'Conciliación $type';
  }

  @override
  String warningsVarianceLabel(String value) {
    return '$value % de variación';
  }

  @override
  String get erpStatusTitle => 'Estado del ERP';

  @override
  String get erpStatusReference => 'Referencia del ERP';

  @override
  String get erpStatusDocumentId => 'ID de documento';

  @override
  String get erpStatusError => 'Error';

  @override
  String get erpStatusLastUpdate => 'Última actualización';

  @override
  String get erpStatusStatus => 'Estado';

  @override
  String get fileViewerPdfTitle => 'PDF de la factura';

  @override
  String get fileViewerImageTitle => 'Imagen de la factura';

  @override
  String get fileViewerPdfError => 'No se pudo cargar el PDF';

  @override
  String get fileViewerImageError => 'No se pudo cargar la imagen';

  @override
  String get fileViewerRetry => 'Reintentar';

  @override
  String get timelineNoActivity => 'Aún no hay actividad';

  @override
  String get payTitle => 'Pagar';

  @override
  String get payTabQueue => 'Cola';

  @override
  String get payTabRuns => 'Lotes';

  @override
  String get paySummaryTotalPaid => 'Total pagado';

  @override
  String get paySummaryPending => 'Pendiente';

  @override
  String get paySummaryInQueue => 'En cola';

  @override
  String get paySummaryCardRebates => 'Reembolsos de tarjeta';

  @override
  String paySummaryPaymentsSubtitle(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count pagos',
      one: '$count pago',
    );
    return '$_temp0';
  }

  @override
  String get payQueueEmpty => 'No hay facturas pendientes de pago';

  @override
  String get payQueueError => 'No se pudo cargar la cola de pagos';

  @override
  String get payQueueRetry => 'Reintentar';

  @override
  String payQueueDue(String date) {
    return 'Vence $date';
  }

  @override
  String get payQueueNoDueDate => 'Sin fecha de vencimiento';

  @override
  String payQueueDiscount(String amount) {
    return 'descuento $amount';
  }

  @override
  String get payQueueOverdue => 'vencida';

  @override
  String get payQueueSelected => 'seleccionada';

  @override
  String payMethodLabel(String invoiceNumber) {
    return 'Método de pago para $invoiceNumber';
  }

  @override
  String get payMethodAch => 'ACH';

  @override
  String get payMethodWire => 'Transferencia';

  @override
  String get payMethodCheck => 'Cheque';

  @override
  String get payMethodVirtualCard => 'Tarjeta virtual';

  @override
  String paySelectedCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count facturas seleccionadas',
      one: '$count factura seleccionada',
    );
    return '$_temp0';
  }

  @override
  String get payClear => 'Borrar';

  @override
  String get payCreateRun => 'Crear lote';

  @override
  String payCreateRunFailed(String error) {
    return 'No se pudo crear el lote: $error';
  }

  @override
  String get payRunsEmpty => 'No hay lotes de pago';

  @override
  String payRunSubtitle(int count, String date) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count pagos',
      one: '$count pago',
    );
    return '$_temp0 • $date';
  }

  @override
  String get payRunCfoRequiredSuffix => ' • Se requiere aprobación del CFO';

  @override
  String payRunAnnounce(String amount, String status, String subtitle) {
    return 'Lote $amount, $status, $subtitle';
  }

  @override
  String get payRunActions => 'Acciones del lote';

  @override
  String get payRunActionExecute => 'Ejecutar';

  @override
  String get payRunActionCancel => 'Cancelar';

  @override
  String get payRunCfoBlocked =>
      'Este lote necesita la aprobación del CFO antes de poder ejecutarse.';

  @override
  String get payRunExecuteTitle => '¿Ejecutar el lote de pago?';

  @override
  String payRunExecuteBody(String amount) {
    return 'Esto envía $amount mediante el procesador de pagos configurado.';
  }

  @override
  String payRunExecuteFailed(String error) {
    return 'Error al ejecutar: $error';
  }

  @override
  String payRunCancelFailed(String error) {
    return 'Error al cancelar: $error';
  }

  @override
  String get payRunStatusDraft => 'Borrador';

  @override
  String get payRunStatusCompleted => 'Completado';

  @override
  String get payRunStatusSubmitted => 'Enviado';

  @override
  String get payRunStatusPartial => 'Parcial';

  @override
  String get payRunStatusFailed => 'Fallido';

  @override
  String get payRunStatusCancelled => 'Cancelado';

  @override
  String get payConfirmCancel => 'Cancelar';

  @override
  String get payConfirmExecute => 'Ejecutar';

  @override
  String get loginAppName => 'Better AP';

  @override
  String get loginTagline => 'Cuentas por pagar, simplificadas';

  @override
  String get loginTenant => 'Inquilino';

  @override
  String get loginEmail => 'Correo electrónico';

  @override
  String get loginPassword => 'Contraseña';

  @override
  String get loginShowPassword => 'Mostrar contraseña';

  @override
  String get loginHidePassword => 'Ocultar contraseña';

  @override
  String get loginRequired => 'Obligatorio';

  @override
  String get loginSignIn => 'Iniciar sesión';

  @override
  String get mfaTitle => 'Autenticación de dos factores';

  @override
  String get mfaHeading => 'Verifica tu identidad';

  @override
  String get mfaPromptEmail =>
      'Introduce el código de 6 dígitos que te enviamos por correo electrónico.';

  @override
  String get mfaPromptTotp =>
      'Introduce el código de 6 dígitos de tu aplicación de autenticación.';

  @override
  String get mfaEnforcedNotice =>
      'Tu organización requiere autenticación de dos factores. Verifica ahora con un código por correo electrónico y luego termina de configurar una aplicación de autenticación en la aplicación web.';

  @override
  String get mfaCode => 'Código';

  @override
  String get mfaCodeRequired => 'Obligatorio';

  @override
  String get mfaCodeTooShort => 'Introduce al menos 6 dígitos';

  @override
  String get mfaVerify => 'Verificar';

  @override
  String get mfaSending => 'Enviando…';

  @override
  String get mfaResendEmailCode => 'Reenviar código por correo';

  @override
  String get mfaSendEmailCode => 'Enviar código por correo';

  @override
  String get mfaUseEmailInstead => 'Usar un código por correo en su lugar';

  @override
  String get mfaUseAuthenticatorInstead =>
      'Usar la aplicación de autenticación en su lugar';

  @override
  String get mfaEmailedAnnounce =>
      'Se envió un código de inicio de sesión a tu correo electrónico.';

  @override
  String get adminUsersTitle => 'Gestión de usuarios';

  @override
  String get adminUsersSearchHint => 'Buscar por nombre o correo electrónico';

  @override
  String get adminUsersEmpty => 'No se encontraron usuarios';

  @override
  String get adminUsersLoadError => 'No se pudieron cargar los usuarios';

  @override
  String get adminUsersEditRoles => 'Editar roles';

  @override
  String get adminUsersNoRoles => 'Sin roles';

  @override
  String get adminUsersDeactivate => 'Desactivar usuario';

  @override
  String get adminUsersActivate => 'Activar usuario';

  @override
  String get adminUsersCannotDeactivateSelf =>
      'No puedes desactivar tu propia cuenta';

  @override
  String get adminUsersDeactivateHint =>
      'Los cierra la sesión y bloquea el inicio de sesión';

  @override
  String get adminUsersActivateHint => 'Restaura el acceso de inicio de sesión';

  @override
  String get adminUsersRoleActive => 'activo';

  @override
  String get adminUsersRoleInactive => 'inactivo';

  @override
  String get adminUsersInactiveBadge => 'Inactivo';

  @override
  String adminUsersRolesUpdated(String name) {
    return 'Roles actualizados para $name';
  }

  @override
  String adminUsersRolesUpdateFailed(String error) {
    return 'No se pudieron actualizar los roles: $error';
  }

  @override
  String adminUsersActivated(String name) {
    return '$name activado';
  }

  @override
  String adminUsersDeactivated(String name) {
    return '$name desactivado';
  }

  @override
  String adminUsersUpdateFailed(String name, String error) {
    return 'No se pudo actualizar $name: $error';
  }

  @override
  String get adminUsersCreateUser => 'Crear usuario';

  @override
  String get adminUsersCreateTitle => 'Nuevo usuario';

  @override
  String get adminUsersFieldFullName => 'Nombre completo';

  @override
  String get adminUsersFieldEmail => 'Correo electrónico';

  @override
  String get adminUsersFieldRoles => 'Roles';

  @override
  String get adminUsersValidationNameRequired =>
      'El nombre completo es obligatorio';

  @override
  String get adminUsersValidationEmailRequired =>
      'El correo electrónico es obligatorio';

  @override
  String get adminUsersValidationEmailInvalid =>
      'Introduce una dirección de correo válida';

  @override
  String get adminUsersCreateSubmit => 'Crear';

  @override
  String get adminUsersCreating => 'Creando…';

  @override
  String adminUsersCreated(String name) {
    return '$name creado';
  }

  @override
  String adminUsersCreateFailed(String error) {
    return 'No se pudo crear el usuario: $error';
  }

  @override
  String get adminUsersTempPasswordTitle => 'Usuario creado';

  @override
  String adminUsersTempPasswordBody(String name) {
    return 'Comparte esta contraseña de un solo uso con $name. Se le pedirá que la cambie al iniciar sesión por primera vez. No se mostrará de nuevo.';
  }

  @override
  String get adminUsersDelete => 'Eliminar usuario';

  @override
  String get adminUsersDeleteHint => 'Elimina esta cuenta de forma permanente';

  @override
  String get adminUsersCannotDeleteSelf =>
      'No puedes eliminar tu propia cuenta';

  @override
  String adminUsersDeleteConfirmTitle(String name) {
    return '¿Eliminar a $name?';
  }

  @override
  String adminUsersDeleteConfirmBody(String name, String email) {
    return 'Esto elimina permanentemente a $name ($email). No se puede deshacer.';
  }

  @override
  String adminUsersDeleted(String name) {
    return '$name eliminado';
  }

  @override
  String adminUsersDeleteFailed(String name, String error) {
    return 'No se pudo eliminar a $name: $error';
  }

  @override
  String get orgSettingsTitle => 'Configuración de la organización';

  @override
  String get orgSettingsNoSettings => 'Sin configuración';

  @override
  String get orgSettingsLoadError => 'No se pudo cargar la configuración';

  @override
  String get orgSettingsSectionCompany => 'Empresa';

  @override
  String get orgSettingsSectionInvoiceDefaults =>
      'Valores predeterminados de factura';

  @override
  String get orgSettingsName => 'Nombre de la organización';

  @override
  String get orgSettingsAddress => 'Dirección';

  @override
  String get orgSettingsPhone => 'Teléfono';

  @override
  String get orgSettingsWebsite => 'Sitio web';

  @override
  String get orgSettingsTaxId => 'Identificación fiscal';

  @override
  String get orgSettingsCurrency => 'Moneda predeterminada';

  @override
  String get orgSettingsPaymentTerms => 'Condiciones de pago';

  @override
  String get orgSettingsNumberPrefix => 'Prefijo de número de factura';

  @override
  String get orgSettingsGlAccount => 'Cuenta contable predeterminada';

  @override
  String get orgSettingsCostCenter => 'Centro de costos predeterminado';

  @override
  String get orgSettingsSave => 'Guardar cambios';

  @override
  String get orgSettingsSaving => 'Guardando…';

  @override
  String orgSettingsFieldRequired(String label) {
    return '$label es obligatorio';
  }

  @override
  String get orgSettingsSaved => 'Configuración de la organización guardada';

  @override
  String orgSettingsSaveFailed(String error) {
    return 'Error al guardar: $error';
  }

  @override
  String get workflowsTitle => 'Flujos de trabajo';

  @override
  String get workflowsEmpty => 'No se encontraron flujos de trabajo';

  @override
  String get workflowsLoadError =>
      'No se pudieron cargar los flujos de trabajo';

  @override
  String get workflowsStatusActive => 'Activo';

  @override
  String get workflowsStatusInactive => 'Inactivo';

  @override
  String get workflowsDefault => 'Predeterminado';

  @override
  String workflowsStepCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count pasos',
      one: '$count paso',
    );
    return '$_temp0';
  }

  @override
  String get workflowDetailFallbackTitle => 'Flujo de trabajo';

  @override
  String get workflowDetailLoadError => 'No se pudo cargar el flujo de trabajo';

  @override
  String get workflowDetailNoSteps => 'Este flujo de trabajo no tiene pasos.';

  @override
  String get workflowDetailDefaultWorkflow => 'Flujo de trabajo predeterminado';

  @override
  String workflowDetailStepNumber(int number) {
    return 'Paso $number';
  }

  @override
  String get workflowDetailStepEnabled => 'Habilitado';

  @override
  String get workflowDetailStepDisabled => 'Deshabilitado';

  @override
  String workflowDetailApproverCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count aprobadores',
      one: '$count aprobador',
    );
    return '$_temp0';
  }

  @override
  String workflowDetailDelaySummary(String hours) {
    return 'Retraso $hours h';
  }

  @override
  String workflowDetailConditionSummary(String field) {
    return 'En $field';
  }
}
