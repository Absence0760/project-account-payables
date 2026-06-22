import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/models/organization.dart';
import 'package:ap_mobile/stores/org_settings_store.dart';

Map<String, dynamic> _orgJson({
  String name = 'Acme Corp',
  Map<String, dynamic>? company,
  Map<String, dynamic>? defaults,
  Map<String, dynamic>? extra,
}) =>
    {
      'id': 'org1',
      'name': name,
      'slug': 'acme',
      'plan': 'pro',
      'created_at': '2026-01-01T00:00:00',
      'settings': {
        'company': company ??
            {
              'address': '1 Main St',
              'phone': '555-0100',
              'website': 'https://acme.test',
              'tax_id': '12-3456789',
              'logo_url': 'https://acme.test/logo.png',
            },
        'invoice_defaults': defaults ??
            {
              'currency': 'USD',
              'payment_terms': 'Net 30',
              'number_prefix': 'INV-',
            },
        ...?extra,
      },
    };

http.Response _ok(Map<String, dynamic> body) => http.Response(
      jsonEncode(body),
      200,
      headers: {'content-type': 'application/json'},
    );

void main() {
  final store = OrgSettingsStore.instance;

  setUp(() {
    store.debugReset();
    ApiClient().debugConfigure();
  });

  group('fetch', () {
    test('projects the settings JSONB to the editable subset', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => _ok(_orgJson())),
      );

      await store.fetch();

      final s = store.settings!;
      expect(s.name, 'Acme Corp');
      expect(s.companyAddress, '1 Main St');
      expect(s.companyTaxId, '12-3456789');
      expect(s.companyLogoUrl, 'https://acme.test/logo.png');
      expect(s.defaultCurrency, 'USD');
      expect(s.invoiceNumberPrefix, 'INV-');
      expect(store.error, isNull);
    });

    test('records the error on failure', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      await store.fetch();

      expect(store.settings, isNull);
      expect(store.error, isNotNull);
    });
  });

  group('save', () {
    test('PATCHes name + the company/invoice_defaults subtrees', () async {
      Map<String, dynamic>? body;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'PATCH') {
            body = jsonDecode(req.body) as Map<String, dynamic>;
            return _ok(_orgJson(name: 'Acme Renamed'));
          }
          return _ok(_orgJson());
        }),
      );

      const update = OrgSettingsUpdate(
        name: 'Acme Renamed',
        companyAddress: '2 New Ave',
        companyPhone: '555-0200',
        companyWebsite: 'https://acme.test',
        companyTaxId: '99-9999999',
        companyLogoUrl: 'https://acme.test/logo.png',
        defaultCurrency: 'EUR',
        defaultPaymentTerms: 'Net 45',
        invoiceNumberPrefix: 'AP-',
        defaultGlAccount: '6000',
        defaultCostCenter: 'CC-1',
      );

      final ok = await store.save(update);

      expect(ok, isTrue);
      expect(body!['name'], 'Acme Renamed');
      final settings = body!['settings'] as Map<String, dynamic>;
      // Only the two safe subtrees are sent — never erp/payments/extraction.
      expect(settings.keys, containsAll(['company', 'invoice_defaults']));
      expect(settings.keys, isNot(contains('erp')));
      final company = settings['company'] as Map<String, dynamic>;
      expect(company['address'], '2 New Ave');
      // The unedited logo_url is carried through so the subtree replace on the
      // backend doesn't drop the web-set logo.
      expect(company['logo_url'], 'https://acme.test/logo.png');
      final defaults = settings['invoice_defaults'] as Map<String, dynamic>;
      expect(defaults['currency'], 'EUR');
      expect(defaults['number_prefix'], 'AP-');
    });

    test('save failure returns false + records the error', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('forbidden', 403)),
      );

      const update = OrgSettingsUpdate(
        name: 'X',
        companyAddress: '',
        companyPhone: '',
        companyWebsite: '',
        companyTaxId: '',
        companyLogoUrl: '',
        defaultCurrency: 'USD',
        defaultPaymentTerms: 'Net 30',
        invoiceNumberPrefix: 'INV-',
        defaultGlAccount: '',
        defaultCostCenter: '',
      );

      final ok = await store.save(update);

      expect(ok, isFalse);
      expect(store.error, isNotNull);
    });
  });
}
