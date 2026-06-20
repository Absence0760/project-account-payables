/// The safe, editable subset of organization settings the mobile app exposes —
/// the company profile and invoice defaults (mirrors the web Org Settings page's
/// least-sensitive tabs). ERP credentials, payment/webhook secrets, extraction
/// keys and SSO live in the same `settings` JSONB but are deliberately NOT
/// surfaced or sent from mobile.
class OrgSettings {
  final String id;
  final String name;
  final String slug;
  final String plan;

  // company.*
  final String companyAddress;
  final String companyPhone;
  final String companyWebsite;
  final String companyTaxId;
  // Carried through (not edited on mobile) so a company-subtree replace on
  // save doesn't drop the white-label logo the web app set.
  final String companyLogoUrl;

  // invoice_defaults.*
  final String defaultCurrency;
  final String defaultPaymentTerms;
  final String invoiceNumberPrefix;
  final String defaultGlAccount;
  final String defaultCostCenter;

  const OrgSettings({
    required this.id,
    required this.name,
    required this.slug,
    required this.plan,
    required this.companyAddress,
    required this.companyPhone,
    required this.companyWebsite,
    required this.companyTaxId,
    required this.companyLogoUrl,
    required this.defaultCurrency,
    required this.defaultPaymentTerms,
    required this.invoiceNumberPrefix,
    required this.defaultGlAccount,
    required this.defaultCostCenter,
  });

  factory OrgSettings.fromJson(Map<String, dynamic> json) {
    final settings = (json['settings'] as Map<String, dynamic>?) ?? const {};
    final company = (settings['company'] as Map<String, dynamic>?) ?? const {};
    final defaults =
        (settings['invoice_defaults'] as Map<String, dynamic>?) ?? const {};

    String s(Map<String, dynamic> m, String k) => (m[k] as String?) ?? '';

    return OrgSettings(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? '',
      slug: json['slug'] as String? ?? '',
      plan: json['plan'] as String? ?? '',
      companyAddress: s(company, 'address'),
      companyPhone: s(company, 'phone'),
      companyWebsite: s(company, 'website'),
      companyTaxId: s(company, 'tax_id'),
      companyLogoUrl: s(company, 'logo_url'),
      defaultCurrency: (defaults['currency'] as String?) ?? 'USD',
      defaultPaymentTerms: (defaults['payment_terms'] as String?) ?? 'Net 30',
      invoiceNumberPrefix: (defaults['number_prefix'] as String?) ?? 'INV-',
      defaultGlAccount: s(defaults, 'default_gl_account'),
      defaultCostCenter: s(defaults, 'default_cost_center'),
    );
  }
}

/// The partial PATCH body for an org-settings edit. Sends `name` plus a
/// `settings` patch carrying ONLY the `company` + `invoice_defaults` sub-trees
/// (the backend merges top-level keys, so untouched keys like `erp` survive).
class OrgSettingsUpdate {
  final String name;
  final String companyAddress;
  final String companyPhone;
  final String companyWebsite;
  final String companyTaxId;
  final String companyLogoUrl;
  final String defaultCurrency;
  final String defaultPaymentTerms;
  final String invoiceNumberPrefix;
  final String defaultGlAccount;
  final String defaultCostCenter;

  const OrgSettingsUpdate({
    required this.name,
    required this.companyAddress,
    required this.companyPhone,
    required this.companyWebsite,
    required this.companyTaxId,
    required this.companyLogoUrl,
    required this.defaultCurrency,
    required this.defaultPaymentTerms,
    required this.invoiceNumberPrefix,
    required this.defaultGlAccount,
    required this.defaultCostCenter,
  });

  Map<String, dynamic> toJson() => {
        'name': name,
        // The backend's UpdateOrganizationRequest shallow-merges these top-level
        // settings keys into the existing dict, so we send the whole `company`
        // and `invoice_defaults` sub-objects (their own fields are replaced).
        'settings': {
          'company': {
            'address': companyAddress,
            'phone': companyPhone,
            'website': companyWebsite,
            'tax_id': companyTaxId,
            'logo_url': companyLogoUrl,
          },
          'invoice_defaults': {
            'currency': defaultCurrency,
            'payment_terms': defaultPaymentTerms,
            'number_prefix': invoiceNumberPrefix,
            'default_gl_account': defaultGlAccount,
            'default_cost_center': defaultCostCenter,
          },
        },
      };
}
