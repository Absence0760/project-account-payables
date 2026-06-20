import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'package:ap_mobile/models/organization.dart';
import 'package:ap_mobile/stores/org_settings_store.dart';
import 'package:ap_mobile/utils/a11y.dart';

/// Admin — organization settings. Reads + edits the safe subset of org settings
/// the web app exposes: the company profile (address / phone / website / tax id)
/// and invoice defaults (currency / payment terms / number prefix / default GL /
/// cost center). ERP credentials, payment/webhook secrets, extraction keys and
/// SSO are deliberately NOT surfaced here. Admin-only (the backend PATCH is
/// `require_roles(ROLE_ADMIN)`); the Settings entry point is admin-gated.
class OrgSettingsScreen extends StatefulWidget {
  const OrgSettingsScreen({super.key});

  @override
  State<OrgSettingsScreen> createState() => _OrgSettingsScreenState();
}

class _OrgSettingsScreenState extends State<OrgSettingsScreen> {
  final _formKey = GlobalKey<FormState>();

  final _name = TextEditingController();
  final _address = TextEditingController();
  final _phone = TextEditingController();
  final _website = TextEditingController();
  final _taxId = TextEditingController();
  final _currency = TextEditingController();
  final _paymentTerms = TextEditingController();
  final _numberPrefix = TextEditingController();
  final _glAccount = TextEditingController();
  final _costCenter = TextEditingController();

  // The loaded settings carry fields we don't edit (logo_url) but must preserve
  // on save, so we keep the last-loaded object around.
  OrgSettings? _loaded;
  bool _seeded = false;

  @override
  void initState() {
    super.initState();
    SchedulerBinding.instance.addPostFrameCallback((_) {
      OrgSettingsStore.instance.fetch();
    });
  }

  @override
  void dispose() {
    for (final c in [
      _name,
      _address,
      _phone,
      _website,
      _taxId,
      _currency,
      _paymentTerms,
      _numberPrefix,
      _glAccount,
      _costCenter,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  void _seed(OrgSettings s) {
    _loaded = s;
    _name.text = s.name;
    _address.text = s.companyAddress;
    _phone.text = s.companyPhone;
    _website.text = s.companyWebsite;
    _taxId.text = s.companyTaxId;
    _currency.text = s.defaultCurrency;
    _paymentTerms.text = s.defaultPaymentTerms;
    _numberPrefix.text = s.invoiceNumberPrefix;
    _glAccount.text = s.defaultGlAccount;
    _costCenter.text = s.defaultCostCenter;
    _seeded = true;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Organization Settings')),
      body: ListenableBuilder(
        listenable: OrgSettingsStore.instance,
        builder: (context, _) {
          final store = OrgSettingsStore.instance;

          if (store.loading && store.settings == null) {
            return const Center(child: CircularProgressIndicator());
          }
          if (store.error != null && store.settings == null) {
            return _ErrorState(message: store.error!, onRetry: store.fetch);
          }
          final settings = store.settings;
          if (settings == null) {
            return const Center(child: Text('No settings'));
          }

          // Seed the form once from the first load (and re-seed if the slug
          // changes, e.g. after a re-login to a different tenant).
          if (!_seeded || _loaded?.id != settings.id) {
            _seed(settings);
          }

          return Form(
            key: _formKey,
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _sectionHeader('Company'),
                _field(_name, 'Organization name', requiredField: true),
                _field(_address, 'Address'),
                _field(_phone, 'Phone', keyboard: TextInputType.phone),
                _field(_website, 'Website', keyboard: TextInputType.url),
                _field(_taxId, 'Tax ID'),
                const SizedBox(height: 16),
                _sectionHeader('Invoice defaults'),
                _field(_currency, 'Default currency'),
                _field(_paymentTerms, 'Payment terms'),
                _field(_numberPrefix, 'Invoice number prefix'),
                _field(_glAccount, 'Default GL account'),
                _field(_costCenter, 'Default cost center'),
                const SizedBox(height: 24),
                FilledButton.icon(
                  onPressed: store.saving ? null : _save,
                  icon: store.saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.save),
                  label: Text(store.saving ? 'Saving…' : 'Save changes'),
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _sectionHeader(String title) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(
          title,
          style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
        ),
      );

  Widget _field(
    TextEditingController controller,
    String label, {
    bool requiredField = false,
    TextInputType? keyboard,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextFormField(
        controller: controller,
        keyboardType: keyboard,
        decoration: InputDecoration(
          labelText: requiredField ? '$label *' : label,
          border: const OutlineInputBorder(),
        ),
        validator: requiredField
            ? (v) =>
                (v == null || v.trim().isEmpty) ? '$label is required' : null
            : null,
      ),
    );
  }

  Future<void> _save() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;

    final update = OrgSettingsUpdate(
      name: _name.text.trim(),
      companyAddress: _address.text.trim(),
      companyPhone: _phone.text.trim(),
      companyWebsite: _website.text.trim(),
      companyTaxId: _taxId.text.trim(),
      // Preserve the web-set logo we don't edit on mobile.
      companyLogoUrl: _loaded?.companyLogoUrl ?? '',
      defaultCurrency: _currency.text.trim(),
      defaultPaymentTerms: _paymentTerms.text.trim(),
      invoiceNumberPrefix: _numberPrefix.text.trim(),
      defaultGlAccount: _glAccount.text.trim(),
      defaultCostCenter: _costCenter.text.trim(),
    );

    final ok = await OrgSettingsStore.instance.save(update);
    if (!mounted) return;
    final message = ok
        ? 'Organization settings saved'
        : 'Failed to save: ${OrgSettingsStore.instance.error ?? ''}';
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
    A11y.announce(context, message);
  }
}

class _ErrorState extends StatelessWidget {
  final String message;
  final Future<void> Function() onRetry;

  const _ErrorState({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.error_outline, size: 48, color: Colors.red.shade700),
          const SizedBox(height: 12),
          const Text('Could not load settings'),
          const SizedBox(height: 12),
          FilledButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    );
  }
}
