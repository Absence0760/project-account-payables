// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Portuguese (`pt`).
class AppLocalizationsPt extends AppLocalizations {
  AppLocalizationsPt([String locale = 'pt']) : super(locale);

  @override
  String get navDashboard => 'Painel';

  @override
  String get navInvoices => 'Faturas';

  @override
  String get navContracts => 'Contratos';

  @override
  String get navApprovals => 'Aprovações';

  @override
  String get navExceptions => 'Exceções';

  @override
  String get navVendors => 'Fornecedores';

  @override
  String get navPay => 'Pagar';

  @override
  String get navPayments => 'Pagamentos';

  @override
  String get navSettings => 'Configurações';

  @override
  String get shellAppName => 'Account Payables';

  @override
  String get commonSave => 'Salvar';

  @override
  String get commonSaving => 'Salvando…';

  @override
  String get commonCancel => 'Cancelar';

  @override
  String get commonLoading => 'Carregando…';

  @override
  String get commonRetry => 'Tentar novamente';

  @override
  String get commonAll => 'Todas';

  @override
  String get commonSearch => 'Pesquisar';

  @override
  String get commonClear => 'Limpar';

  @override
  String get commonApply => 'Aplicar';

  @override
  String get commonClose => 'Fechar';

  @override
  String get settingsTitle => 'Configurações';

  @override
  String get settingsTenant => 'Organização';

  @override
  String get settingsTenantNotSet => 'Não definido';

  @override
  String get settingsApiServer => 'Servidor da API';

  @override
  String get settingsBiometricUnlock => 'Desbloqueio biométrico';

  @override
  String get settingsBiometricHint =>
      'Usar impressão digital ou rosto para desbloquear';

  @override
  String get settingsSignOut => 'Sair';

  @override
  String get settingsLanguage => 'Idioma';

  @override
  String get settingsLanguageHint =>
      'Escolha o idioma usado em todo o aplicativo. Sua escolha é salva neste dispositivo.';

  @override
  String get settingsLanguageSystem => 'Padrão do sistema';

  @override
  String get dashboardTitle => 'Painel';

  @override
  String get dashboardTotalInvoices => 'Total de faturas';

  @override
  String get dashboardUpcoming => 'A vencer';

  @override
  String get dashboardForReview => 'Para revisão';

  @override
  String get dashboardApproved => 'Aprovadas';

  @override
  String get dashboardAging => 'Idade das faturas';

  @override
  String get dashboardTopVendors => 'Principais fornecedores';

  @override
  String get dashboardAgingCurrent => 'Em dia';

  @override
  String get dashboardAgingDays30 => '30 dias';

  @override
  String get dashboardAgingDays60 => '60 dias';

  @override
  String get dashboardAgingDays90plus => '90+';

  @override
  String get dashboardCachedBanner =>
      'Dados em cache — não foi possível conectar ao servidor';

  @override
  String dashboardErrorPrefix(String error) {
    return 'Erro: $error';
  }

  @override
  String dashboardInvoiceCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count faturas',
      one: '$count fatura',
    );
    return '$_temp0';
  }

  @override
  String get invoicesTitle => 'Faturas';

  @override
  String get invoicesSearchHint => 'Pesquisar faturas…';

  @override
  String get invoicesSearchAria => 'Pesquisar faturas';

  @override
  String get invoicesAdvancedSearch => 'Pesquisa avançada';

  @override
  String get invoicesAdvancedSearchActive =>
      'Pesquisa avançada, filtros ativos';

  @override
  String get invoicesCaptureInvoice => 'Capturar fatura';

  @override
  String get invoicesCaptureInvoiceLabel => 'Capturar fatura';

  @override
  String get invoicesEmpty => 'Nenhuma fatura encontrada';

  @override
  String get invoicesFilterAll => 'Todas';

  @override
  String get invoicesFilterNew => 'Novas';

  @override
  String get invoicesFilterPending => 'Pendentes';

  @override
  String get invoicesFilterReview => 'Revisão';

  @override
  String get invoicesFilterApproved => 'Aprovadas';

  @override
  String get invoicesFilterRejected => 'Rejeitadas';

  @override
  String get invoicesFilterPaid => 'Pagas';

  @override
  String get invoicesColInvoiceNumber => 'N.º da fatura';

  @override
  String get invoicesColVendor => 'Fornecedor';

  @override
  String get invoicesColAmount => 'Valor';

  @override
  String get invoicesColDueDate => 'Vencimento';

  @override
  String get invoicesColStatus => 'Status';

  @override
  String get notificationsTitle => 'Notificações';

  @override
  String get notificationsMarkAllRead => 'Marcar tudo como lido';

  @override
  String get notificationsMarkAllReadLabel =>
      'Marcar todas as notificações como lidas';

  @override
  String get notificationsFilterUnread => 'Não lidas';

  @override
  String get notificationsAllMarkedRead =>
      'Todas as notificações marcadas como lidas';

  @override
  String get notificationsCouldNotMarkAll =>
      'Não foi possível marcar tudo como lido';

  @override
  String get notificationsEmptyUnread => 'Nenhuma notificação não lida';

  @override
  String get notificationsEmpty => 'Nenhuma notificação';

  @override
  String get notificationsCaughtUp => 'Você está em dia';

  @override
  String get notificationsNothingYet => 'Nada por aqui ainda';

  @override
  String get notificationsLoadError =>
      'Não foi possível carregar as notificações';

  @override
  String get vendorsTitle => 'Fornecedores';

  @override
  String get vendorsSyncErp => 'Sincronizar do ERP';

  @override
  String get vendorsSyncErpLabel => 'Sincronizar fornecedores do ERP';

  @override
  String get vendorsSearchHint => 'Pesquisar fornecedores…';

  @override
  String get vendorsFilterUnverified => 'Não verificados';

  @override
  String get vendorsFilterActive => 'Ativos';

  @override
  String get vendorsFilterInactive => 'Inativos';

  @override
  String get vendorsFilterRejected => 'Rejeitados';

  @override
  String get vendorsEmpty => 'Nenhum fornecedor encontrado';

  @override
  String get vendorsLoadError => 'Não foi possível carregar os fornecedores';

  @override
  String get vendorActionVerify => 'Verificar';

  @override
  String get vendorActionReject => 'Rejeitar';

  @override
  String get vendorUnverifiedLabel => 'Fornecedor não verificado';

  @override
  String get vendorVerifyHint => 'Tornar elegível para pagamento';

  @override
  String get vendorRejectHint => 'Marcar como inválido / duplicado';

  @override
  String get vendorVerified => 'Fornecedor verificado';

  @override
  String get vendorRejected => 'Fornecedor rejeitado';

  @override
  String get vendorActionFailed => 'Falha na ação';

  @override
  String vendorSyncFailed(String error) {
    return 'Falha na sincronização com o ERP: $error';
  }

  @override
  String get exceptionsTitle => 'Exceções';

  @override
  String get exceptionsFilterOpen => 'Abertas';

  @override
  String get exceptionsFilterEscalated => 'Escaladas';

  @override
  String get exceptionsFilterResolved => 'Resolvidas';

  @override
  String get exceptionsFilterDismissed => 'Descartadas';

  @override
  String get exceptionsEmpty => 'Nenhuma exceção';

  @override
  String get exceptionsQueueClear => 'A fila de exceções está vazia';

  @override
  String get exceptionActionResolve => 'Resolver';

  @override
  String get exceptionActionEscalate => 'Escalar';

  @override
  String get exceptionActionDismiss => 'Descartar';

  @override
  String get exceptionResolved => 'Exceção resolvida';

  @override
  String get exceptionEscalated => 'Exceção escalada';

  @override
  String get exceptionDismissed => 'Exceção descartada';

  @override
  String get exceptionActionFailed => 'Falha na ação';

  @override
  String get paymentsTitle => 'Pagamentos';

  @override
  String get paymentsEmpty => 'Nenhum pagamento';

  @override
  String paymentsErrorPrefix(String error) {
    return 'Erro: $error';
  }

  @override
  String get paymentStatusPending => 'Pendente';

  @override
  String get paymentStatusProcessing => 'Em processamento';

  @override
  String get paymentStatusCompleted => 'Concluído';

  @override
  String get paymentStatusFailed => 'Falhou';

  @override
  String get paymentStatusCancelled => 'Cancelado';

  @override
  String get approvalsTitle => 'Aprovações pendentes';

  @override
  String get approvalsAllCaughtUp => 'Tudo em dia!';

  @override
  String get approvalsNoneWaiting => 'Nenhuma fatura aguardando aprovação';

  @override
  String approvalsPendingCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count faturas pendentes',
      one: '$count fatura pendente',
    );
    return '$_temp0';
  }

  @override
  String get approvalActionApprove => 'Aprovar';

  @override
  String get approvalActionReject => 'Rejeitar';

  @override
  String get approvalApproved => 'Fatura aprovada';

  @override
  String get captureTitle => 'Capturar fatura';

  @override
  String get captureChange => 'Alterar';

  @override
  String get captureUpload => 'Enviar';

  @override
  String get captureUploading => 'Enviando…';

  @override
  String get captureEmptyPrompt =>
      'Tire uma foto, escolha da galeria ou selecione um arquivo';

  @override
  String get captureCamera => 'Câmera';

  @override
  String get captureGallery => 'Galeria';

  @override
  String get captureChooseFile => 'Escolher arquivo';

  @override
  String get captureSupportedFormats => 'Compatível com PDF, PNG, JPG e TIFF';

  @override
  String get captureUploadSuccess => 'Fatura enviada com sucesso';

  @override
  String captureUploadFailedStatus(int status, String message) {
    return 'Falha no envio ($status): $message';
  }

  @override
  String captureUploadFailed(String error) {
    return 'Falha no envio: $error';
  }

  @override
  String captureSelectedDocument(String name) {
    return 'Documento selecionado: $name';
  }

  @override
  String get capturePdfReady => 'Documento PDF pronto para envio';

  @override
  String get advSearchTitle => 'Busca avançada';

  @override
  String get advSearchClose => 'Fechar busca avançada';

  @override
  String get advSearchVendor => 'Fornecedor';

  @override
  String get advSearchPoNumber => 'Número do pedido';

  @override
  String get advSearchMinAmount => 'Valor mínimo';

  @override
  String get advSearchMaxAmount => 'Valor máximo';

  @override
  String get advSearchDueFrom => 'Vencimento de';

  @override
  String get advSearchDueTo => 'Vencimento até';

  @override
  String get advSearchAny => 'Qualquer';

  @override
  String get advSearchInvalidAmount => 'Insira um valor válido (ex.: 1000)';

  @override
  String get advSearchMinMaxError => 'O mínimo não deve exceder o máximo';

  @override
  String advSearchClearField(String label) {
    return 'Limpar $label';
  }

  @override
  String advSearchDateFieldHint(String label, String value) {
    return '$label, atualmente $value. Toque duas vezes para alterar.';
  }

  @override
  String get invoiceDetailTitle => 'Detalhe da fatura';

  @override
  String get invoiceDetailEdit => 'Editar';

  @override
  String get invoiceDetailEditLabel => 'Editar fatura';

  @override
  String get invoiceDetailRetry => 'Tentar novamente';

  @override
  String invoiceDetailErrorPrefix(String error) {
    return 'Erro: $error';
  }

  @override
  String get invoiceDetailNoChanges => 'Nenhuma alteração para salvar';

  @override
  String get invoiceDetailUpdated => 'Fatura atualizada';

  @override
  String get invoiceDetailUpdateFailed =>
      'Não foi possível salvar as alterações — tente novamente';

  @override
  String get invoiceDetailApproved => 'Fatura aprovada';

  @override
  String get invoiceDetailApproveFailed =>
      'Não foi possível aprovar a fatura — tente novamente';

  @override
  String get invoiceDetailRejected => 'Fatura rejeitada';

  @override
  String get invoiceDetailRejectFailed =>
      'Não foi possível rejeitar a fatura — tente novamente';

  @override
  String get invoiceDetailRejectTitle => 'Rejeitar fatura';

  @override
  String get invoiceDetailRejectReason => 'Motivo';

  @override
  String get invoiceDetailReject => 'Rejeitar';

  @override
  String get invoiceDetailApprove => 'Aprovar';

  @override
  String get invoiceDetailUnknownVendor => 'Fornecedor desconhecido';

  @override
  String get invoiceDetailFieldInvoiceNumber => 'N.º da fatura';

  @override
  String get invoiceDetailFieldPoNumber => 'N.º do pedido';

  @override
  String get invoiceDetailFieldCurrency => 'Moeda';

  @override
  String get invoiceDetailFieldInvoiceDate => 'Data da fatura';

  @override
  String get invoiceDetailFieldDueDate => 'Data de vencimento';

  @override
  String get invoiceDetailFieldDescription => 'Descrição';

  @override
  String get invoiceDetailFieldGlAccount => 'Conta contábil';

  @override
  String get invoiceDetailFieldCreated => 'Criada';

  @override
  String get invoiceDetailActivity => 'Atividade';

  @override
  String get invoiceDetailActivityError =>
      'Não foi possível carregar a atividade';

  @override
  String get invoiceDetailFilePdfLabel =>
      'PDF da fatura. Toque duas vezes para ver em tela cheia.';

  @override
  String get invoiceDetailFileLabel =>
      'Arquivo da fatura. Toque duas vezes para ver em tela cheia.';

  @override
  String get invoiceDetailTapToViewPdf => 'Toque para ver o PDF';

  @override
  String get invoiceDetailTapToViewFile => 'Toque para ver o arquivo';

  @override
  String get invoiceEditTitle => 'Editar fatura';

  @override
  String get invoiceEditClose => 'Fechar o formulário de edição';

  @override
  String get invoiceEditVendor => 'Fornecedor';

  @override
  String get invoiceEditInvoiceNumber => 'N.º da fatura';

  @override
  String get invoiceEditAmount => 'Valor';

  @override
  String get invoiceEditPoNumber => 'N.º do pedido';

  @override
  String get invoiceEditGlAccount => 'Conta contábil';

  @override
  String get invoiceEditDescription => 'Descrição';

  @override
  String get invoiceEditDueDate => 'Data de vencimento';

  @override
  String get invoiceEditNotSet => 'Não definida';

  @override
  String get invoiceEditInvalidAmount =>
      'Insira um valor válido (ex.: 1234,56)';

  @override
  String get invoiceEditClearDueDate => 'Limpar data de vencimento';

  @override
  String invoiceEditDueDateHint(String value) {
    return 'Data de vencimento, atualmente $value. Toque duas vezes para alterar.';
  }

  @override
  String get warningsSectionTitle => 'Avisos e alertas de fraude';

  @override
  String get warningsPoMatchTitle => 'Conciliação de pedido';

  @override
  String get warningsSeverityError => 'Erro';

  @override
  String get warningsSeverityWarning => 'Aviso';

  @override
  String get warningsSeverityInfo => 'Informação';

  @override
  String get warningsPoLabel => 'Pedido';

  @override
  String warningsMatchLabel(String type) {
    return 'Conciliação $type';
  }

  @override
  String warningsVarianceLabel(String value) {
    return '$value% de variação';
  }

  @override
  String get erpStatusTitle => 'Status do ERP';

  @override
  String get erpStatusReference => 'Referência do ERP';

  @override
  String get erpStatusDocumentId => 'ID do documento';

  @override
  String get erpStatusError => 'Erro';

  @override
  String get erpStatusLastUpdate => 'Última atualização';

  @override
  String get erpStatusStatus => 'Status';

  @override
  String get fileViewerPdfTitle => 'PDF da fatura';

  @override
  String get fileViewerImageTitle => 'Imagem da fatura';

  @override
  String get fileViewerPdfError => 'Não foi possível carregar o PDF';

  @override
  String get fileViewerImageError => 'Não foi possível carregar a imagem';

  @override
  String get fileViewerRetry => 'Tentar novamente';

  @override
  String get timelineNoActivity => 'Nenhuma atividade ainda';

  @override
  String get payTitle => 'Pagar';

  @override
  String get payTabQueue => 'Fila';

  @override
  String get payTabRuns => 'Lotes';

  @override
  String get paySummaryTotalPaid => 'Total pago';

  @override
  String get paySummaryPending => 'Pendente';

  @override
  String get paySummaryInQueue => 'Na fila';

  @override
  String get paySummaryCardRebates => 'Reembolsos de cartão';

  @override
  String paySummaryPaymentsSubtitle(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count pagamentos',
      one: '$count pagamento',
    );
    return '$_temp0';
  }

  @override
  String get payQueueEmpty => 'Nenhuma fatura aguardando pagamento';

  @override
  String get payQueueError => 'Não foi possível carregar a fila de pagamentos';

  @override
  String get payQueueRetry => 'Tentar novamente';

  @override
  String payQueueDue(String date) {
    return 'Vence em $date';
  }

  @override
  String get payQueueNoDueDate => 'Sem data de vencimento';

  @override
  String payQueueDiscount(String amount) {
    return 'desconto $amount';
  }

  @override
  String get payQueueOverdue => 'vencida';

  @override
  String get payQueueSelected => 'selecionada';

  @override
  String payMethodLabel(String invoiceNumber) {
    return 'Forma de pagamento para $invoiceNumber';
  }

  @override
  String get payMethodAch => 'ACH';

  @override
  String get payMethodWire => 'Transferência';

  @override
  String get payMethodCheck => 'Cheque';

  @override
  String get payMethodVirtualCard => 'Cartão virtual';

  @override
  String paySelectedCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count faturas selecionadas',
      one: '$count fatura selecionada',
    );
    return '$_temp0';
  }

  @override
  String get payClear => 'Limpar';

  @override
  String get payCreateRun => 'Criar lote';

  @override
  String payCreateRunFailed(String error) {
    return 'Falha ao criar o lote: $error';
  }

  @override
  String get payRunsEmpty => 'Nenhum lote de pagamento';

  @override
  String payRunSubtitle(int count, String date) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count pagamentos',
      one: '$count pagamento',
    );
    return '$_temp0 • $date';
  }

  @override
  String get payRunCfoRequiredSuffix => ' • Aprovação do CFO necessária';

  @override
  String payRunAnnounce(String amount, String status, String subtitle) {
    return 'Lote $amount, $status, $subtitle';
  }

  @override
  String get payRunActions => 'Ações do lote';

  @override
  String get payRunActionExecute => 'Executar';

  @override
  String get payRunActionCancel => 'Cancelar';

  @override
  String get payRunCfoBlocked =>
      'Este lote precisa da aprovação do CFO antes de poder ser executado.';

  @override
  String get payRunExecuteTitle => 'Executar o lote de pagamento?';

  @override
  String payRunExecuteBody(String amount) {
    return 'Isso envia $amount pelo processador de pagamentos configurado.';
  }

  @override
  String payRunExecuteFailed(String error) {
    return 'Falha ao executar: $error';
  }

  @override
  String payRunCancelFailed(String error) {
    return 'Falha ao cancelar: $error';
  }

  @override
  String get payRunStatusDraft => 'Rascunho';

  @override
  String get payRunStatusCompleted => 'Concluído';

  @override
  String get payRunStatusSubmitted => 'Enviado';

  @override
  String get payRunStatusPartial => 'Parcial';

  @override
  String get payRunStatusFailed => 'Falhou';

  @override
  String get payRunStatusCancelled => 'Cancelado';

  @override
  String get payConfirmCancel => 'Cancelar';

  @override
  String get payConfirmExecute => 'Executar';

  @override
  String get loginAppName => 'Better AP';

  @override
  String get loginTagline => 'Contas a pagar, simplificadas';

  @override
  String get loginTenant => 'Inquilino';

  @override
  String get loginEmail => 'E-mail';

  @override
  String get loginPassword => 'Senha';

  @override
  String get loginShowPassword => 'Mostrar senha';

  @override
  String get loginHidePassword => 'Ocultar senha';

  @override
  String get loginRequired => 'Obrigatório';

  @override
  String get loginSignIn => 'Entrar';

  @override
  String get mfaTitle => 'Autenticação de dois fatores';

  @override
  String get mfaHeading => 'Verifique sua identidade';

  @override
  String get mfaPromptEmail =>
      'Digite o código de 6 dígitos que enviamos por e-mail.';

  @override
  String get mfaPromptTotp =>
      'Digite o código de 6 dígitos do seu aplicativo autenticador.';

  @override
  String get mfaEnforcedNotice =>
      'Sua organização exige autenticação de dois fatores. Verifique agora com um código por e-mail e depois conclua a configuração de um aplicativo autenticador no aplicativo web.';

  @override
  String get mfaCode => 'Código';

  @override
  String get mfaCodeRequired => 'Obrigatório';

  @override
  String get mfaCodeTooShort => 'Digite ao menos 6 dígitos';

  @override
  String get mfaVerify => 'Verificar';

  @override
  String get mfaSending => 'Enviando…';

  @override
  String get mfaResendEmailCode => 'Reenviar código por e-mail';

  @override
  String get mfaSendEmailCode => 'Enviar código por e-mail';

  @override
  String get mfaUseEmailInstead => 'Usar um código por e-mail em vez disso';

  @override
  String get mfaUseAuthenticatorInstead =>
      'Usar o aplicativo autenticador em vez disso';

  @override
  String get mfaEmailedAnnounce =>
      'Um código de login foi enviado para o seu e-mail.';

  @override
  String get adminUsersTitle => 'Gerenciamento de usuários';

  @override
  String get adminUsersSearchHint => 'Pesquisar por nome ou e-mail';

  @override
  String get adminUsersEmpty => 'Nenhum usuário encontrado';

  @override
  String get adminUsersLoadError => 'Não foi possível carregar os usuários';

  @override
  String get adminUsersEditRoles => 'Editar funções';

  @override
  String get adminUsersNoRoles => 'Sem funções';

  @override
  String get adminUsersDeactivate => 'Desativar usuário';

  @override
  String get adminUsersActivate => 'Ativar usuário';

  @override
  String get adminUsersCannotDeactivateSelf =>
      'Você não pode desativar sua própria conta';

  @override
  String get adminUsersDeactivateHint =>
      'Desconecta o usuário e bloqueia o login';

  @override
  String get adminUsersActivateHint => 'Restaura o acesso de login';

  @override
  String get adminUsersRoleActive => 'ativo';

  @override
  String get adminUsersRoleInactive => 'inativo';

  @override
  String get adminUsersInactiveBadge => 'Inativo';

  @override
  String adminUsersRolesUpdated(String name) {
    return 'Funções atualizadas para $name';
  }

  @override
  String adminUsersRolesUpdateFailed(String error) {
    return 'Falha ao atualizar as funções: $error';
  }

  @override
  String adminUsersActivated(String name) {
    return '$name ativado';
  }

  @override
  String adminUsersDeactivated(String name) {
    return '$name desativado';
  }

  @override
  String adminUsersUpdateFailed(String name, String error) {
    return 'Falha ao atualizar $name: $error';
  }

  @override
  String get adminUsersCreateUser => 'Criar utilizador';

  @override
  String get adminUsersCreateTitle => 'Novo utilizador';

  @override
  String get adminUsersFieldFullName => 'Nome completo';

  @override
  String get adminUsersFieldEmail => 'E-mail';

  @override
  String get adminUsersFieldRoles => 'Funções';

  @override
  String get adminUsersValidationNameRequired =>
      'O nome completo é obrigatório';

  @override
  String get adminUsersValidationEmailRequired => 'O e-mail é obrigatório';

  @override
  String get adminUsersValidationEmailInvalid =>
      'Introduza um endereço de e-mail válido';

  @override
  String get adminUsersCreateSubmit => 'Criar';

  @override
  String get adminUsersCreating => 'A criar…';

  @override
  String adminUsersCreated(String name) {
    return '$name criado';
  }

  @override
  String adminUsersCreateFailed(String error) {
    return 'Falha ao criar o utilizador: $error';
  }

  @override
  String get adminUsersTempPasswordTitle => 'Utilizador criado';

  @override
  String adminUsersTempPasswordBody(String name) {
    return 'Partilhe esta palavra-passe de uso único com $name. Ser-lhe-á pedido que a altere no primeiro início de sessão. Não será mostrada novamente.';
  }

  @override
  String get adminUsersDelete => 'Eliminar utilizador';

  @override
  String get adminUsersDeleteHint => 'Remove permanentemente esta conta';

  @override
  String get adminUsersCannotDeleteSelf =>
      'Não pode eliminar a sua própria conta';

  @override
  String adminUsersDeleteConfirmTitle(String name) {
    return 'Eliminar $name?';
  }

  @override
  String adminUsersDeleteConfirmBody(String name, String email) {
    return 'Isto remove permanentemente $name ($email). Não pode ser anulado.';
  }

  @override
  String adminUsersDeleted(String name) {
    return '$name eliminado';
  }

  @override
  String adminUsersDeleteFailed(String name, String error) {
    return 'Falha ao eliminar $name: $error';
  }

  @override
  String get orgSettingsTitle => 'Configurações da organização';

  @override
  String get orgSettingsNoSettings => 'Sem configurações';

  @override
  String get orgSettingsLoadError =>
      'Não foi possível carregar as configurações';

  @override
  String get orgSettingsSectionCompany => 'Empresa';

  @override
  String get orgSettingsSectionInvoiceDefaults => 'Padrões de fatura';

  @override
  String get orgSettingsName => 'Nome da organização';

  @override
  String get orgSettingsAddress => 'Endereço';

  @override
  String get orgSettingsPhone => 'Telefone';

  @override
  String get orgSettingsWebsite => 'Site';

  @override
  String get orgSettingsTaxId => 'Identificação fiscal';

  @override
  String get orgSettingsCurrency => 'Moeda padrão';

  @override
  String get orgSettingsPaymentTerms => 'Condições de pagamento';

  @override
  String get orgSettingsNumberPrefix => 'Prefixo do número da fatura';

  @override
  String get orgSettingsGlAccount => 'Conta contábil padrão';

  @override
  String get orgSettingsCostCenter => 'Centro de custo padrão';

  @override
  String get orgSettingsSave => 'Salvar alterações';

  @override
  String get orgSettingsSaving => 'Salvando…';

  @override
  String orgSettingsFieldRequired(String label) {
    return '$label é obrigatório';
  }

  @override
  String get orgSettingsSaved => 'Configurações da organização salvas';

  @override
  String orgSettingsSaveFailed(String error) {
    return 'Falha ao salvar: $error';
  }

  @override
  String get workflowsTitle => 'Fluxos de trabalho';

  @override
  String get workflowsEmpty => 'Nenhum fluxo de trabalho encontrado';

  @override
  String get workflowsLoadError =>
      'Não foi possível carregar os fluxos de trabalho';

  @override
  String get workflowsStatusActive => 'Ativo';

  @override
  String get workflowsStatusInactive => 'Inativo';

  @override
  String get workflowsDefault => 'Padrão';

  @override
  String workflowsStepCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count etapas',
      one: '$count etapa',
    );
    return '$_temp0';
  }

  @override
  String get workflowDetailFallbackTitle => 'Fluxo de trabalho';

  @override
  String get workflowDetailLoadError =>
      'Não foi possível carregar o fluxo de trabalho';

  @override
  String get workflowDetailNoSteps => 'Este fluxo de trabalho não tem etapas.';

  @override
  String get workflowDetailDefaultWorkflow => 'Fluxo de trabalho padrão';

  @override
  String workflowDetailStepNumber(int number) {
    return 'Etapa $number';
  }

  @override
  String get workflowDetailStepEnabled => 'Ativado';

  @override
  String get workflowDetailStepDisabled => 'Desativado';

  @override
  String workflowDetailApproverCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count aprovadores',
      one: '$count aprovador',
    );
    return '$_temp0';
  }

  @override
  String workflowDetailDelaySummary(String hours) {
    return 'Atraso $hours h';
  }

  @override
  String workflowDetailConditionSummary(String field) {
    return 'Em $field';
  }
}

/// The translations for Portuguese, as used in Brazil (`pt_BR`).
class AppLocalizationsPtBr extends AppLocalizationsPt {
  AppLocalizationsPtBr() : super('pt_BR');

  @override
  String get navDashboard => 'Painel';

  @override
  String get navInvoices => 'Faturas';

  @override
  String get navContracts => 'Contratos';

  @override
  String get navApprovals => 'Aprovações';

  @override
  String get navExceptions => 'Exceções';

  @override
  String get navVendors => 'Fornecedores';

  @override
  String get navPay => 'Pagar';

  @override
  String get navPayments => 'Pagamentos';

  @override
  String get navSettings => 'Configurações';

  @override
  String get shellAppName => 'Account Payables';

  @override
  String get commonSave => 'Salvar';

  @override
  String get commonSaving => 'Salvando…';

  @override
  String get commonCancel => 'Cancelar';

  @override
  String get commonLoading => 'Carregando…';

  @override
  String get commonRetry => 'Tentar novamente';

  @override
  String get commonAll => 'Todas';

  @override
  String get commonSearch => 'Pesquisar';

  @override
  String get commonClear => 'Limpar';

  @override
  String get commonApply => 'Aplicar';

  @override
  String get commonClose => 'Fechar';

  @override
  String get settingsTitle => 'Configurações';

  @override
  String get settingsTenant => 'Organização';

  @override
  String get settingsTenantNotSet => 'Não definido';

  @override
  String get settingsApiServer => 'Servidor da API';

  @override
  String get settingsBiometricUnlock => 'Desbloqueio biométrico';

  @override
  String get settingsBiometricHint =>
      'Usar impressão digital ou rosto para desbloquear';

  @override
  String get settingsSignOut => 'Sair';

  @override
  String get settingsLanguage => 'Idioma';

  @override
  String get settingsLanguageHint =>
      'Escolha o idioma usado em todo o aplicativo. Sua escolha é salva neste dispositivo.';

  @override
  String get settingsLanguageSystem => 'Padrão do sistema';

  @override
  String get dashboardTitle => 'Painel';

  @override
  String get dashboardTotalInvoices => 'Total de faturas';

  @override
  String get dashboardUpcoming => 'A vencer';

  @override
  String get dashboardForReview => 'Para revisão';

  @override
  String get dashboardApproved => 'Aprovadas';

  @override
  String get dashboardAging => 'Idade das faturas';

  @override
  String get dashboardTopVendors => 'Principais fornecedores';

  @override
  String get dashboardAgingCurrent => 'Em dia';

  @override
  String get dashboardAgingDays30 => '30 dias';

  @override
  String get dashboardAgingDays60 => '60 dias';

  @override
  String get dashboardAgingDays90plus => '90+';

  @override
  String get dashboardCachedBanner =>
      'Dados em cache — não foi possível conectar ao servidor';

  @override
  String dashboardErrorPrefix(String error) {
    return 'Erro: $error';
  }

  @override
  String dashboardInvoiceCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count faturas',
      one: '$count fatura',
    );
    return '$_temp0';
  }

  @override
  String get invoicesTitle => 'Faturas';

  @override
  String get invoicesSearchHint => 'Pesquisar faturas…';

  @override
  String get invoicesSearchAria => 'Pesquisar faturas';

  @override
  String get invoicesAdvancedSearch => 'Pesquisa avançada';

  @override
  String get invoicesAdvancedSearchActive =>
      'Pesquisa avançada, filtros ativos';

  @override
  String get invoicesCaptureInvoice => 'Capturar fatura';

  @override
  String get invoicesCaptureInvoiceLabel => 'Capturar fatura';

  @override
  String get invoicesEmpty => 'Nenhuma fatura encontrada';

  @override
  String get invoicesFilterAll => 'Todas';

  @override
  String get invoicesFilterNew => 'Novas';

  @override
  String get invoicesFilterPending => 'Pendentes';

  @override
  String get invoicesFilterReview => 'Revisão';

  @override
  String get invoicesFilterApproved => 'Aprovadas';

  @override
  String get invoicesFilterRejected => 'Rejeitadas';

  @override
  String get invoicesFilterPaid => 'Pagas';

  @override
  String get invoicesColInvoiceNumber => 'N.º da fatura';

  @override
  String get invoicesColVendor => 'Fornecedor';

  @override
  String get invoicesColAmount => 'Valor';

  @override
  String get invoicesColDueDate => 'Vencimento';

  @override
  String get invoicesColStatus => 'Status';

  @override
  String get notificationsTitle => 'Notificações';

  @override
  String get notificationsMarkAllRead => 'Marcar tudo como lido';

  @override
  String get notificationsMarkAllReadLabel =>
      'Marcar todas as notificações como lidas';

  @override
  String get notificationsFilterUnread => 'Não lidas';

  @override
  String get notificationsAllMarkedRead =>
      'Todas as notificações marcadas como lidas';

  @override
  String get notificationsCouldNotMarkAll =>
      'Não foi possível marcar tudo como lido';

  @override
  String get notificationsEmptyUnread => 'Nenhuma notificação não lida';

  @override
  String get notificationsEmpty => 'Nenhuma notificação';

  @override
  String get notificationsCaughtUp => 'Você está em dia';

  @override
  String get notificationsNothingYet => 'Nada por aqui ainda';

  @override
  String get notificationsLoadError =>
      'Não foi possível carregar as notificações';

  @override
  String get vendorsTitle => 'Fornecedores';

  @override
  String get vendorsSyncErp => 'Sincronizar do ERP';

  @override
  String get vendorsSyncErpLabel => 'Sincronizar fornecedores do ERP';

  @override
  String get vendorsSearchHint => 'Pesquisar fornecedores…';

  @override
  String get vendorsFilterUnverified => 'Não verificados';

  @override
  String get vendorsFilterActive => 'Ativos';

  @override
  String get vendorsFilterInactive => 'Inativos';

  @override
  String get vendorsFilterRejected => 'Rejeitados';

  @override
  String get vendorsEmpty => 'Nenhum fornecedor encontrado';

  @override
  String get vendorsLoadError => 'Não foi possível carregar os fornecedores';

  @override
  String get vendorActionVerify => 'Verificar';

  @override
  String get vendorActionReject => 'Rejeitar';

  @override
  String get vendorUnverifiedLabel => 'Fornecedor não verificado';

  @override
  String get vendorVerifyHint => 'Tornar elegível para pagamento';

  @override
  String get vendorRejectHint => 'Marcar como inválido / duplicado';

  @override
  String get vendorVerified => 'Fornecedor verificado';

  @override
  String get vendorRejected => 'Fornecedor rejeitado';

  @override
  String get vendorActionFailed => 'Falha na ação';

  @override
  String vendorSyncFailed(String error) {
    return 'Falha na sincronização com o ERP: $error';
  }

  @override
  String get exceptionsTitle => 'Exceções';

  @override
  String get exceptionsFilterOpen => 'Abertas';

  @override
  String get exceptionsFilterEscalated => 'Escaladas';

  @override
  String get exceptionsFilterResolved => 'Resolvidas';

  @override
  String get exceptionsFilterDismissed => 'Descartadas';

  @override
  String get exceptionsEmpty => 'Nenhuma exceção';

  @override
  String get exceptionsQueueClear => 'A fila de exceções está vazia';

  @override
  String get exceptionActionResolve => 'Resolver';

  @override
  String get exceptionActionEscalate => 'Escalar';

  @override
  String get exceptionActionDismiss => 'Descartar';

  @override
  String get exceptionResolved => 'Exceção resolvida';

  @override
  String get exceptionEscalated => 'Exceção escalada';

  @override
  String get exceptionDismissed => 'Exceção descartada';

  @override
  String get exceptionActionFailed => 'Falha na ação';

  @override
  String get paymentsTitle => 'Pagamentos';

  @override
  String get paymentsEmpty => 'Nenhum pagamento';

  @override
  String paymentsErrorPrefix(String error) {
    return 'Erro: $error';
  }

  @override
  String get paymentStatusPending => 'Pendente';

  @override
  String get paymentStatusProcessing => 'Em processamento';

  @override
  String get paymentStatusCompleted => 'Concluído';

  @override
  String get paymentStatusFailed => 'Falhou';

  @override
  String get paymentStatusCancelled => 'Cancelado';

  @override
  String get approvalsTitle => 'Aprovações pendentes';

  @override
  String get approvalsAllCaughtUp => 'Tudo em dia!';

  @override
  String get approvalsNoneWaiting => 'Nenhuma fatura aguardando aprovação';

  @override
  String approvalsPendingCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count faturas pendentes',
      one: '$count fatura pendente',
    );
    return '$_temp0';
  }

  @override
  String get approvalActionApprove => 'Aprovar';

  @override
  String get approvalActionReject => 'Rejeitar';

  @override
  String get approvalApproved => 'Fatura aprovada';

  @override
  String get captureTitle => 'Capturar fatura';

  @override
  String get captureChange => 'Alterar';

  @override
  String get captureUpload => 'Enviar';

  @override
  String get captureUploading => 'Enviando…';

  @override
  String get captureEmptyPrompt =>
      'Tire uma foto, escolha da galeria ou selecione um arquivo';

  @override
  String get captureCamera => 'Câmera';

  @override
  String get captureGallery => 'Galeria';

  @override
  String get captureChooseFile => 'Escolher arquivo';

  @override
  String get captureSupportedFormats => 'Compatível com PDF, PNG, JPG e TIFF';

  @override
  String get captureUploadSuccess => 'Fatura enviada com sucesso';

  @override
  String captureUploadFailedStatus(int status, String message) {
    return 'Falha no envio ($status): $message';
  }

  @override
  String captureUploadFailed(String error) {
    return 'Falha no envio: $error';
  }

  @override
  String captureSelectedDocument(String name) {
    return 'Documento selecionado: $name';
  }

  @override
  String get capturePdfReady => 'Documento PDF pronto para envio';

  @override
  String get advSearchTitle => 'Busca avançada';

  @override
  String get advSearchClose => 'Fechar busca avançada';

  @override
  String get advSearchVendor => 'Fornecedor';

  @override
  String get advSearchPoNumber => 'Número do pedido';

  @override
  String get advSearchMinAmount => 'Valor mínimo';

  @override
  String get advSearchMaxAmount => 'Valor máximo';

  @override
  String get advSearchDueFrom => 'Vencimento de';

  @override
  String get advSearchDueTo => 'Vencimento até';

  @override
  String get advSearchAny => 'Qualquer';

  @override
  String get advSearchInvalidAmount => 'Insira um valor válido (ex.: 1000)';

  @override
  String get advSearchMinMaxError => 'O mínimo não deve exceder o máximo';

  @override
  String advSearchClearField(String label) {
    return 'Limpar $label';
  }

  @override
  String advSearchDateFieldHint(String label, String value) {
    return '$label, atualmente $value. Toque duas vezes para alterar.';
  }

  @override
  String get invoiceDetailTitle => 'Detalhe da fatura';

  @override
  String get invoiceDetailEdit => 'Editar';

  @override
  String get invoiceDetailEditLabel => 'Editar fatura';

  @override
  String get invoiceDetailRetry => 'Tentar novamente';

  @override
  String invoiceDetailErrorPrefix(String error) {
    return 'Erro: $error';
  }

  @override
  String get invoiceDetailNoChanges => 'Nenhuma alteração para salvar';

  @override
  String get invoiceDetailUpdated => 'Fatura atualizada';

  @override
  String get invoiceDetailUpdateFailed =>
      'Não foi possível salvar as alterações — tente novamente';

  @override
  String get invoiceDetailApproved => 'Fatura aprovada';

  @override
  String get invoiceDetailApproveFailed =>
      'Não foi possível aprovar a fatura — tente novamente';

  @override
  String get invoiceDetailRejected => 'Fatura rejeitada';

  @override
  String get invoiceDetailRejectFailed =>
      'Não foi possível rejeitar a fatura — tente novamente';

  @override
  String get invoiceDetailRejectTitle => 'Rejeitar fatura';

  @override
  String get invoiceDetailRejectReason => 'Motivo';

  @override
  String get invoiceDetailReject => 'Rejeitar';

  @override
  String get invoiceDetailApprove => 'Aprovar';

  @override
  String get invoiceDetailUnknownVendor => 'Fornecedor desconhecido';

  @override
  String get invoiceDetailFieldInvoiceNumber => 'N.º da fatura';

  @override
  String get invoiceDetailFieldPoNumber => 'N.º do pedido';

  @override
  String get invoiceDetailFieldCurrency => 'Moeda';

  @override
  String get invoiceDetailFieldInvoiceDate => 'Data da fatura';

  @override
  String get invoiceDetailFieldDueDate => 'Data de vencimento';

  @override
  String get invoiceDetailFieldDescription => 'Descrição';

  @override
  String get invoiceDetailFieldGlAccount => 'Conta contábil';

  @override
  String get invoiceDetailFieldCreated => 'Criada';

  @override
  String get invoiceDetailActivity => 'Atividade';

  @override
  String get invoiceDetailActivityError =>
      'Não foi possível carregar a atividade';

  @override
  String get invoiceDetailFilePdfLabel =>
      'PDF da fatura. Toque duas vezes para ver em tela cheia.';

  @override
  String get invoiceDetailFileLabel =>
      'Arquivo da fatura. Toque duas vezes para ver em tela cheia.';

  @override
  String get invoiceDetailTapToViewPdf => 'Toque para ver o PDF';

  @override
  String get invoiceDetailTapToViewFile => 'Toque para ver o arquivo';

  @override
  String get invoiceEditTitle => 'Editar fatura';

  @override
  String get invoiceEditClose => 'Fechar o formulário de edição';

  @override
  String get invoiceEditVendor => 'Fornecedor';

  @override
  String get invoiceEditInvoiceNumber => 'N.º da fatura';

  @override
  String get invoiceEditAmount => 'Valor';

  @override
  String get invoiceEditPoNumber => 'N.º do pedido';

  @override
  String get invoiceEditGlAccount => 'Conta contábil';

  @override
  String get invoiceEditDescription => 'Descrição';

  @override
  String get invoiceEditDueDate => 'Data de vencimento';

  @override
  String get invoiceEditNotSet => 'Não definida';

  @override
  String get invoiceEditInvalidAmount =>
      'Insira um valor válido (ex.: 1234,56)';

  @override
  String get invoiceEditClearDueDate => 'Limpar data de vencimento';

  @override
  String invoiceEditDueDateHint(String value) {
    return 'Data de vencimento, atualmente $value. Toque duas vezes para alterar.';
  }

  @override
  String get warningsSectionTitle => 'Avisos e alertas de fraude';

  @override
  String get warningsPoMatchTitle => 'Conciliação de pedido';

  @override
  String get warningsSeverityError => 'Erro';

  @override
  String get warningsSeverityWarning => 'Aviso';

  @override
  String get warningsSeverityInfo => 'Informação';

  @override
  String get warningsPoLabel => 'Pedido';

  @override
  String warningsMatchLabel(String type) {
    return 'Conciliação $type';
  }

  @override
  String warningsVarianceLabel(String value) {
    return '$value% de variação';
  }

  @override
  String get erpStatusTitle => 'Status do ERP';

  @override
  String get erpStatusReference => 'Referência do ERP';

  @override
  String get erpStatusDocumentId => 'ID do documento';

  @override
  String get erpStatusError => 'Erro';

  @override
  String get erpStatusLastUpdate => 'Última atualização';

  @override
  String get erpStatusStatus => 'Status';

  @override
  String get fileViewerPdfTitle => 'PDF da fatura';

  @override
  String get fileViewerImageTitle => 'Imagem da fatura';

  @override
  String get fileViewerPdfError => 'Não foi possível carregar o PDF';

  @override
  String get fileViewerImageError => 'Não foi possível carregar a imagem';

  @override
  String get fileViewerRetry => 'Tentar novamente';

  @override
  String get timelineNoActivity => 'Nenhuma atividade ainda';

  @override
  String get payTitle => 'Pagar';

  @override
  String get payTabQueue => 'Fila';

  @override
  String get payTabRuns => 'Lotes';

  @override
  String get paySummaryTotalPaid => 'Total pago';

  @override
  String get paySummaryPending => 'Pendente';

  @override
  String get paySummaryInQueue => 'Na fila';

  @override
  String get paySummaryCardRebates => 'Reembolsos de cartão';

  @override
  String paySummaryPaymentsSubtitle(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count pagamentos',
      one: '$count pagamento',
    );
    return '$_temp0';
  }

  @override
  String get payQueueEmpty => 'Nenhuma fatura aguardando pagamento';

  @override
  String get payQueueError => 'Não foi possível carregar a fila de pagamentos';

  @override
  String get payQueueRetry => 'Tentar novamente';

  @override
  String payQueueDue(String date) {
    return 'Vence em $date';
  }

  @override
  String get payQueueNoDueDate => 'Sem data de vencimento';

  @override
  String payQueueDiscount(String amount) {
    return 'desconto $amount';
  }

  @override
  String get payQueueOverdue => 'vencida';

  @override
  String get payQueueSelected => 'selecionada';

  @override
  String payMethodLabel(String invoiceNumber) {
    return 'Forma de pagamento para $invoiceNumber';
  }

  @override
  String get payMethodAch => 'ACH';

  @override
  String get payMethodWire => 'Transferência';

  @override
  String get payMethodCheck => 'Cheque';

  @override
  String get payMethodVirtualCard => 'Cartão virtual';

  @override
  String paySelectedCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count faturas selecionadas',
      one: '$count fatura selecionada',
    );
    return '$_temp0';
  }

  @override
  String get payClear => 'Limpar';

  @override
  String get payCreateRun => 'Criar lote';

  @override
  String payCreateRunFailed(String error) {
    return 'Falha ao criar o lote: $error';
  }

  @override
  String get payRunsEmpty => 'Nenhum lote de pagamento';

  @override
  String payRunSubtitle(int count, String date) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count pagamentos',
      one: '$count pagamento',
    );
    return '$_temp0 • $date';
  }

  @override
  String get payRunCfoRequiredSuffix => ' • Aprovação do CFO necessária';

  @override
  String payRunAnnounce(String amount, String status, String subtitle) {
    return 'Lote $amount, $status, $subtitle';
  }

  @override
  String get payRunActions => 'Ações do lote';

  @override
  String get payRunActionExecute => 'Executar';

  @override
  String get payRunActionCancel => 'Cancelar';

  @override
  String get payRunCfoBlocked =>
      'Este lote precisa da aprovação do CFO antes de poder ser executado.';

  @override
  String get payRunExecuteTitle => 'Executar o lote de pagamento?';

  @override
  String payRunExecuteBody(String amount) {
    return 'Isso envia $amount pelo processador de pagamentos configurado.';
  }

  @override
  String payRunExecuteFailed(String error) {
    return 'Falha ao executar: $error';
  }

  @override
  String payRunCancelFailed(String error) {
    return 'Falha ao cancelar: $error';
  }

  @override
  String get payRunStatusDraft => 'Rascunho';

  @override
  String get payRunStatusCompleted => 'Concluído';

  @override
  String get payRunStatusSubmitted => 'Enviado';

  @override
  String get payRunStatusPartial => 'Parcial';

  @override
  String get payRunStatusFailed => 'Falhou';

  @override
  String get payRunStatusCancelled => 'Cancelado';

  @override
  String get payConfirmCancel => 'Cancelar';

  @override
  String get payConfirmExecute => 'Executar';

  @override
  String get loginAppName => 'Better AP';

  @override
  String get loginTagline => 'Contas a pagar, simplificadas';

  @override
  String get loginTenant => 'Inquilino';

  @override
  String get loginEmail => 'E-mail';

  @override
  String get loginPassword => 'Senha';

  @override
  String get loginShowPassword => 'Mostrar senha';

  @override
  String get loginHidePassword => 'Ocultar senha';

  @override
  String get loginRequired => 'Obrigatório';

  @override
  String get loginSignIn => 'Entrar';

  @override
  String get mfaTitle => 'Autenticação de dois fatores';

  @override
  String get mfaHeading => 'Verifique sua identidade';

  @override
  String get mfaPromptEmail =>
      'Digite o código de 6 dígitos que enviamos por e-mail.';

  @override
  String get mfaPromptTotp =>
      'Digite o código de 6 dígitos do seu aplicativo autenticador.';

  @override
  String get mfaEnforcedNotice =>
      'Sua organização exige autenticação de dois fatores. Verifique agora com um código por e-mail e depois conclua a configuração de um aplicativo autenticador no aplicativo web.';

  @override
  String get mfaCode => 'Código';

  @override
  String get mfaCodeRequired => 'Obrigatório';

  @override
  String get mfaCodeTooShort => 'Digite ao menos 6 dígitos';

  @override
  String get mfaVerify => 'Verificar';

  @override
  String get mfaSending => 'Enviando…';

  @override
  String get mfaResendEmailCode => 'Reenviar código por e-mail';

  @override
  String get mfaSendEmailCode => 'Enviar código por e-mail';

  @override
  String get mfaUseEmailInstead => 'Usar um código por e-mail em vez disso';

  @override
  String get mfaUseAuthenticatorInstead =>
      'Usar o aplicativo autenticador em vez disso';

  @override
  String get mfaEmailedAnnounce =>
      'Um código de login foi enviado para o seu e-mail.';

  @override
  String get adminUsersTitle => 'Gerenciamento de usuários';

  @override
  String get adminUsersSearchHint => 'Pesquisar por nome ou e-mail';

  @override
  String get adminUsersEmpty => 'Nenhum usuário encontrado';

  @override
  String get adminUsersLoadError => 'Não foi possível carregar os usuários';

  @override
  String get adminUsersEditRoles => 'Editar funções';

  @override
  String get adminUsersNoRoles => 'Sem funções';

  @override
  String get adminUsersDeactivate => 'Desativar usuário';

  @override
  String get adminUsersActivate => 'Ativar usuário';

  @override
  String get adminUsersCannotDeactivateSelf =>
      'Você não pode desativar sua própria conta';

  @override
  String get adminUsersDeactivateHint =>
      'Desconecta o usuário e bloqueia o login';

  @override
  String get adminUsersActivateHint => 'Restaura o acesso de login';

  @override
  String get adminUsersRoleActive => 'ativo';

  @override
  String get adminUsersRoleInactive => 'inativo';

  @override
  String get adminUsersInactiveBadge => 'Inativo';

  @override
  String adminUsersRolesUpdated(String name) {
    return 'Funções atualizadas para $name';
  }

  @override
  String adminUsersRolesUpdateFailed(String error) {
    return 'Falha ao atualizar as funções: $error';
  }

  @override
  String adminUsersActivated(String name) {
    return '$name ativado';
  }

  @override
  String adminUsersDeactivated(String name) {
    return '$name desativado';
  }

  @override
  String adminUsersUpdateFailed(String name, String error) {
    return 'Falha ao atualizar $name: $error';
  }

  @override
  String get adminUsersCreateUser => 'Criar usuário';

  @override
  String get adminUsersCreateTitle => 'Novo usuário';

  @override
  String get adminUsersFieldFullName => 'Nome completo';

  @override
  String get adminUsersFieldEmail => 'E-mail';

  @override
  String get adminUsersFieldRoles => 'Funções';

  @override
  String get adminUsersValidationNameRequired =>
      'O nome completo é obrigatório';

  @override
  String get adminUsersValidationEmailRequired => 'O e-mail é obrigatório';

  @override
  String get adminUsersValidationEmailInvalid =>
      'Insira um endereço de e-mail válido';

  @override
  String get adminUsersCreateSubmit => 'Criar';

  @override
  String get adminUsersCreating => 'Criando…';

  @override
  String adminUsersCreated(String name) {
    return '$name criado';
  }

  @override
  String adminUsersCreateFailed(String error) {
    return 'Falha ao criar o usuário: $error';
  }

  @override
  String get adminUsersTempPasswordTitle => 'Usuário criado';

  @override
  String adminUsersTempPasswordBody(String name) {
    return 'Compartilhe esta senha de uso único com $name. Será solicitado que a altere no primeiro login. Ela não será mostrada novamente.';
  }

  @override
  String get adminUsersDelete => 'Excluir usuário';

  @override
  String get adminUsersDeleteHint => 'Remove permanentemente esta conta';

  @override
  String get adminUsersCannotDeleteSelf =>
      'Você não pode excluir sua própria conta';

  @override
  String adminUsersDeleteConfirmTitle(String name) {
    return 'Excluir $name?';
  }

  @override
  String adminUsersDeleteConfirmBody(String name, String email) {
    return 'Isso remove permanentemente $name ($email). Não pode ser desfeito.';
  }

  @override
  String adminUsersDeleted(String name) {
    return '$name excluído';
  }

  @override
  String adminUsersDeleteFailed(String name, String error) {
    return 'Falha ao excluir $name: $error';
  }

  @override
  String get orgSettingsTitle => 'Configurações da organização';

  @override
  String get orgSettingsNoSettings => 'Sem configurações';

  @override
  String get orgSettingsLoadError =>
      'Não foi possível carregar as configurações';

  @override
  String get orgSettingsSectionCompany => 'Empresa';

  @override
  String get orgSettingsSectionInvoiceDefaults => 'Padrões de fatura';

  @override
  String get orgSettingsName => 'Nome da organização';

  @override
  String get orgSettingsAddress => 'Endereço';

  @override
  String get orgSettingsPhone => 'Telefone';

  @override
  String get orgSettingsWebsite => 'Site';

  @override
  String get orgSettingsTaxId => 'Identificação fiscal';

  @override
  String get orgSettingsCurrency => 'Moeda padrão';

  @override
  String get orgSettingsPaymentTerms => 'Condições de pagamento';

  @override
  String get orgSettingsNumberPrefix => 'Prefixo do número da fatura';

  @override
  String get orgSettingsGlAccount => 'Conta contábil padrão';

  @override
  String get orgSettingsCostCenter => 'Centro de custo padrão';

  @override
  String get orgSettingsSave => 'Salvar alterações';

  @override
  String get orgSettingsSaving => 'Salvando…';

  @override
  String orgSettingsFieldRequired(String label) {
    return '$label é obrigatório';
  }

  @override
  String get orgSettingsSaved => 'Configurações da organização salvas';

  @override
  String orgSettingsSaveFailed(String error) {
    return 'Falha ao salvar: $error';
  }

  @override
  String get workflowsTitle => 'Fluxos de trabalho';

  @override
  String get workflowsEmpty => 'Nenhum fluxo de trabalho encontrado';

  @override
  String get workflowsLoadError =>
      'Não foi possível carregar os fluxos de trabalho';

  @override
  String get workflowsStatusActive => 'Ativo';

  @override
  String get workflowsStatusInactive => 'Inativo';

  @override
  String get workflowsDefault => 'Padrão';

  @override
  String workflowsStepCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count etapas',
      one: '$count etapa',
    );
    return '$_temp0';
  }

  @override
  String get workflowDetailFallbackTitle => 'Fluxo de trabalho';

  @override
  String get workflowDetailLoadError =>
      'Não foi possível carregar o fluxo de trabalho';

  @override
  String get workflowDetailNoSteps => 'Este fluxo de trabalho não tem etapas.';

  @override
  String get workflowDetailDefaultWorkflow => 'Fluxo de trabalho padrão';

  @override
  String workflowDetailStepNumber(int number) {
    return 'Etapa $number';
  }

  @override
  String get workflowDetailStepEnabled => 'Ativado';

  @override
  String get workflowDetailStepDisabled => 'Desativado';

  @override
  String workflowDetailApproverCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count aprovadores',
      one: '$count aprovador',
    );
    return '$_temp0';
  }

  @override
  String workflowDetailDelaySummary(String hours) {
    return 'Atraso $hours h';
  }

  @override
  String workflowDetailConditionSummary(String field) {
    return 'Em $field';
  }
}
