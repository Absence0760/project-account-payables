import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/vendor_store.dart';

http.Response _list(List<Map<String, dynamic>> items) => http.Response(
      jsonEncode({'items': items, 'total': items.length, 'page': 1}),
      200,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _vendorJson(
  String id, {
  String status = 'unverified',
  String source = 'manual',
}) =>
    {
      'id': id,
      'name': 'Vendor $id',
      'code': 'V$id',
      'email': 'v$id@example.com',
      'status': status,
      'source': source,
      'invoice_count': 2,
      'created_at': '2026-01-01T12:00:00',
    };

void main() {
  final store = VendorStore.instance;

  setUpAll(() async {
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    VendorStore.instance.debugReset();
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
  });

  group('fetch', () {
    test('success populates vendors and marks them live (not cached)',
        () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([_vendorJson('1')])),
      );

      await store.fetch();

      expect(store.vendors, hasLength(1));
      expect(store.vendors.first.id, '1');
      expect(store.fromCache, isFalse);
      expect(store.error, isNull);
      expect(store.loading, isFalse);
    });

    test('falls back to the offline cache when the network fails', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([_vendorJson('1')])),
      );
      await store.fetch();
      expect(store.fromCache, isFalse);

      ApiClient().debugConfigure(
        client: MockClient((req) async => throw Exception('offline')),
      );
      await store.fetch();

      expect(store.vendors, hasLength(1));
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
    test('setStatusFilter carries the status into the request', () async {
      final sent = Completer<String?>();
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (!sent.isCompleted) {
            sent.complete(req.url.queryParameters['status']);
          }
          return _list([]);
        }),
      );

      store.setStatusFilter('unverified');

      expect(store.statusFilter, 'unverified');
      expect(await sent.future, 'unverified');
    });

    test('setSearch carries the query into the request', () async {
      final sent = Completer<String?>();
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (!sent.isCompleted) {
            sent.complete(req.url.queryParameters['search']);
          }
          return _list([]);
        }),
      );

      store.setSearch('acme');

      expect(await sent.future, 'acme');
    });
  });

  group('actions', () {
    test('verify posts to /verify and refetches', () async {
      var verifyCalls = 0;
      var listCalls = 0;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/verify')) {
            verifyCalls++;
            return http.Response(
              jsonEncode(_vendorJson('1', status: 'active')),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          listCalls++;
          return _list([_vendorJson('1', status: 'active')]);
        }),
      );

      final ok = await store.verify('1');

      expect(ok, isTrue);
      expect(verifyCalls, 1);
      expect(listCalls, 1, reason: 'verify should trigger a refetch');
    });

    test('reject posts to /reject and refetches', () async {
      var rejectCalls = 0;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/reject')) {
            rejectCalls++;
            return http.Response(
              jsonEncode(_vendorJson('1', status: 'rejected')),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          return _list([_vendorJson('1', status: 'rejected')]);
        }),
      );

      final ok = await store.reject('1');

      expect(ok, isTrue);
      expect(rejectCalls, 1);
    });

    test('verify returns false + records error on failure', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      final ok = await store.verify('1');

      expect(ok, isFalse);
      expect(store.error, isNotNull);
    });

    test('syncErp posts to /sync-erp and returns the server message', () async {
      var syncCalls = 0;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/sync-erp')) {
            syncCalls++;
            return http.Response(
              jsonEncode({
                'success': true,
                'message': 'Synced 2 new, 0 updated, 0 unchanged',
                'created': 2,
              }),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          return _list([_vendorJson('1', source: 'erp_sync')]);
        }),
      );

      final message = await store.syncErp();

      expect(syncCalls, 1);
      expect(message, contains('Synced 2 new'));
    });

    test('syncErp returns null + records error when no ERP configured (400)',
        () async {
      ApiClient().debugConfigure(
        client: MockClient(
          (req) async => http.Response('No ERP configured', 400),
        ),
      );

      final message = await store.syncErp();

      expect(message, isNull);
      expect(store.error, isNotNull);
    });
  });
}
