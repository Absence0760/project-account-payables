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
}
