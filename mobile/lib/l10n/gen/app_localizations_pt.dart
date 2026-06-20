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
}
