import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/exception_store.dart';

http.Response _list(List<Map<String, dynamic>> items) => http.Response(
      jsonEncode({'items': items, 'total': items.length, 'page': 1}),
      200,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _exceptionJson(
  String id, {
  String status = 'open',
  String type = 'duplicate',
  String severity = 'error',
}) =>
    {
      'id': id,
      'invoice_id': 'inv-$id',
      'invoice_number': 'INV-$id',
      'vendor_name': 'Acme',
      'amount': 250,
      'exception_type': type,
      'type_label': 'Duplicate Invoice',
      'severity': severity,
      'description': 'Looks like a dupe',
      'status': status,
      'is_overdue': false,
      'created_at': '2026-01-01T12:00:00',
    };

void main() {
  final store = ExceptionStore.instance;

  setUpAll(() async {
    // Private in-memory cache so parallel test isolates don't share a file.
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    ExceptionStore.instance.debugReset();
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
  });

  group('fetch', () {
    test('success populates exceptions and marks them live (not cached)',
        () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([_exceptionJson('1')])),
      );

      await store.fetch();

      expect(store.exceptions, hasLength(1));
      expect(store.exceptions.first.id, '1');
      expect(store.exceptions.first.typeLabel, 'Duplicate Invoice');
      expect(store.fromCache, isFalse);
      expect(store.error, isNull);
      expect(store.loading, isFalse);
    });

    test('falls back to the offline cache when the network fails', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([_exceptionJson('1')])),
      );
      await store.fetch();
      expect(store.fromCache, isFalse);

      ApiClient().debugConfigure(
        client: MockClient((req) async => throw Exception('offline')),
      );
      await store.fetch();

      expect(store.exceptions, hasLength(1));
      expect(store.exceptions.first.id, '1');
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

      store.setStatusFilter('open');

      expect(store.statusFilter, 'open');
      expect(await sentStatus.future, 'open');
    });
  });

  group('actions', () {
    test('resolve posts the resolve action and then refetches', () async {
      var resolveCalls = 0;
      var listCalls = 0;
      String? sentAction;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/resolve')) {
            resolveCalls++;
            sentAction =
                (jsonDecode(req.body) as Map<String, dynamic>)['action']
                    as String?;
            return http.Response(
              jsonEncode({'id': '1', 'status': 'resolved'}),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          listCalls++;
          return _list([_exceptionJson('1', status: 'resolved')]);
        }),
      );

      final ok = await store.resolve('1');

      expect(ok, isTrue);
      expect(resolveCalls, 1);
      expect(sentAction, 'resolve');
      expect(listCalls, 1, reason: 'resolve should trigger a refetch');
    });

    test('escalate posts the escalate action', () async {
      String? sentAction;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/resolve')) {
            sentAction =
                (jsonDecode(req.body) as Map<String, dynamic>)['action']
                    as String?;
            return http.Response('{}', 200);
          }
          return _list([]);
        }),
      );

      final ok = await store.escalate('1');

      expect(ok, isTrue);
      expect(sentAction, 'escalate');
    });

    test('dismiss posts the dismiss action', () async {
      String? sentAction;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/resolve')) {
            sentAction =
                (jsonDecode(req.body) as Map<String, dynamic>)['action']
                    as String?;
            return http.Response('{}', 200);
          }
          return _list([]);
        }),
      );

      final ok = await store.dismiss('1');

      expect(ok, isTrue);
      expect(sentAction, 'dismiss');
    });

    test('an action returns false and records the error on failure', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      final ok = await store.resolve('1');

      expect(ok, isFalse);
      expect(store.error, isNotNull);
    });
  });
}
