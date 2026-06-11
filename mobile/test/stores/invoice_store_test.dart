import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/invoice_store.dart';

http.Response _list(List<Map<String, dynamic>> items) => http.Response(
      jsonEncode({'invoices': items}),
      200,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _invoiceJson(
  String id, {
  String status = 'pending',
  String vendor = 'Acme',
}) =>
    {
      'id': id,
      'invoice_number': 'INV-$id',
      'vendor_name': vendor,
      'amount': 100,
      'currency': 'USD',
      'status': status,
      'created_at': '2026-01-01T12:00:00',
    };

void main() {
  final store = InvoiceStore.instance;

  setUpAll(() async {
    // Private in-memory cache so parallel test isolates don't share a file.
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    InvoiceStore.instance.debugReset();
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
  });

  group('fetch', () {
    test('success populates invoices and marks them as live (not cached)',
        () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([_invoiceJson('1')])),
      );

      await store.fetch();

      expect(store.invoices, hasLength(1));
      expect(store.invoices.first.id, '1');
      expect(store.fromCache, isFalse);
      expect(store.error, isNull);
      expect(store.loading, isFalse);
    });

    test('falls back to the offline cache when the network fails', () async {
      // 1) prime the cache with a successful fetch
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([_invoiceJson('1')])),
      );
      await store.fetch();
      expect(store.fromCache, isFalse);

      // 2) same filters, but the network now fails -> serve from cache
      ApiClient().debugConfigure(
        client: MockClient((req) async => throw Exception('offline')),
      );
      await store.fetch();

      expect(store.invoices, hasLength(1));
      expect(store.invoices.first.id, '1');
      expect(store.fromCache, isTrue);
    });

    test('surfaces an error when the network fails and no cache exists',
        () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => throw Exception('offline')),
      );

      await store.fetch();

      expect(store.error, isNotNull);
      expect(store.fromCache, isFalse);
    });
  });

  group('filters', () {
    test('pendingApproval returns only ready_for_review invoices', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([
              _invoiceJson('1', status: 'ready_for_review'),
              _invoiceJson('2', status: 'pending'),
              _invoiceJson('3', status: 'ready_for_review'),
            ])),
      );

      await store.fetch();

      expect(store.invoices, hasLength(3));
      expect(
        store.pendingApproval.map((i) => i.id),
        ['1', '3'],
      );
    });

    test('setStatusFilter updates the getter and carries it into the request',
        () async {
      final sentStatus = Completer<String?>();
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (!sentStatus.isCompleted) {
            sentStatus.complete(req.url.queryParameters['status']);
          }
          return _list([]);
        }),
      );

      store.setStatusFilter('approved');

      expect(store.statusFilter, 'approved');
      expect(await sentStatus.future, 'approved');
    });
  });

  group('approve / reject', () {
    test('approve posts and then refetches the list', () async {
      var approveCalls = 0;
      var listCalls = 0;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/approve')) {
            approveCalls++;
            return http.Response(
              jsonEncode(_invoiceJson('1', status: 'approved')),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          listCalls++;
          return _list([_invoiceJson('1', status: 'approved')]);
        }),
      );

      final ok = await store.approve('1');

      expect(ok, isTrue);
      expect(approveCalls, 1);
      expect(listCalls, 1, reason: 'approve should trigger a refetch');
    });

    test('approve returns false and records the error on failure', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      final ok = await store.approve('1');

      expect(ok, isFalse);
      expect(store.error, isNotNull);
    });

    test('reject posts the reason and refetches', () async {
      String? sentReason;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/reject')) {
            sentReason = (jsonDecode(req.body) as Map)['reason'] as String?;
            return http.Response(
              jsonEncode(_invoiceJson('1', status: 'rejected')),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          return _list([]);
        }),
      );

      final ok = await store.reject('1', 'Wrong amount');

      expect(ok, isTrue);
      expect(sentReason, 'Wrong amount');
    });
  });
}
