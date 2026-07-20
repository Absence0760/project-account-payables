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
    InvoiceStore.instance.reset();
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

    test('setFilters carries the advanced-search params into the request',
        () async {
      final sent = Completer<Map<String, String>>();
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (!sent.isCompleted) sent.complete(req.url.queryParameters);
          return _list([]);
        }),
      );

      store.setFilters(const InvoiceSearchFilters(
        vendor: 'Acme',
        poNumber: 'PO-9',
        amountMin: 100,
        amountMax: 5000,
      ));

      expect(store.filters.isEmpty, isFalse);
      expect(store.filters.activeCount, 4);
      final params = await sent.future;
      expect(params['vendor'], 'Acme');
      expect(params['po_number'], 'PO-9');
      expect(params['amount_min'], '100.0');
      expect(params['amount_max'], '5000.0');
    });

    test('setFilters sends due-date range as YYYY-MM-DD', () async {
      final sent = Completer<Map<String, String>>();
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (!sent.isCompleted) sent.complete(req.url.queryParameters);
          return _list([]);
        }),
      );

      store.setFilters(InvoiceSearchFilters(
        dueDateFrom: DateTime(2026, 2, 1),
        dueDateTo: DateTime(2026, 3, 15),
      ));

      final params = await sent.future;
      expect(params['due_date_from'], '2026-02-01');
      expect(params['due_date_to'], '2026-03-15');
    });

    test('setFilters with empty omits all advanced params', () async {
      final sent = Completer<Map<String, String>>();
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (!sent.isCompleted) sent.complete(req.url.queryParameters);
          return _list([]);
        }),
      );

      store.setFilters(InvoiceSearchFilters.empty);

      expect(store.filters.isEmpty, isTrue);
      final params = await sent.future;
      expect(params.containsKey('vendor'), isFalse);
      expect(params.containsKey('amount_min'), isFalse);
      expect(params.containsKey('due_date_from'), isFalse);
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

  group('update (edit fields)', () {
    test('PATCHes the partial body and refetches the list', () async {
      String? method;
      String? path;
      Map<String, dynamic>? sentBody;
      var listCalls = 0;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'PATCH') {
            method = req.method;
            path = req.url.path;
            sentBody = jsonDecode(req.body) as Map<String, dynamic>;
            return http.Response(
              jsonEncode(_invoiceJson('1', vendor: 'Globex')),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          listCalls++;
          return _list([_invoiceJson('1', vendor: 'Globex')]);
        }),
      );

      final updated = await store.update('1', {
        'vendor': 'Globex',
        'amount': '250.00', // string-Decimal, never a float
      });

      expect(updated, isNotNull);
      expect(updated!.vendorName, 'Globex');
      expect(method, 'PATCH');
      expect(path, '/api/invoices/1');
      // Money goes over the wire as a string (Decimal-safe), not a JS number.
      expect(sentBody!['amount'], '250.00');
      expect(sentBody!['amount'], isA<String>());
      expect(listCalls, 1, reason: 'a successful edit refetches the list');
    });

    test('returns null and records the error on failure', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 409)),
      );

      final updated = await store.update('1', {'vendor': 'X'});

      expect(updated, isNull);
      expect(store.error, isNotNull);
    });
  });

  group('fetchAuditLog (activity timeline)', () {
    test('parses the bare audit-log array oldest-first', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          expect(req.url.path, '/api/invoices/1/audit-log');
          return http.Response(
            jsonEncode([
              {
                'id': 'a1',
                'actor_id': 'u1',
                'actor_name': 'Demo User',
                'action': 'invoice.uploaded',
                'entity_type': 'invoice',
                'entity_id': '1',
                'details': null,
                'created_at': '2026-01-01T10:00:00',
              },
              {
                'id': 'a2',
                'actor_id': 'u1',
                'actor_name': 'Demo User',
                'action': 'invoice.edited',
                'entity_type': 'invoice',
                'entity_id': '1',
                'details': {
                  'changes': {
                    'amount': {'old': '100.00', 'new': '250.00'},
                  },
                },
                'created_at': '2026-01-02T10:00:00',
              },
            ]),
            200,
            headers: {'content-type': 'application/json'},
          );
        }),
      );

      final entries = await store.fetchAuditLog('1');

      expect(entries, hasLength(2));
      expect(entries.first.action, 'invoice.uploaded');
      expect(entries.first.actionLabel, 'Uploaded invoice');
      final edit = entries[1];
      expect(edit.changes, hasLength(1));
      expect(edit.changes.first.field, 'amount');
      expect(edit.changes.first.oldDisplay, '100.00');
      expect(edit.changes.first.newDisplay, '250.00');
    });

    test('rethrows when the request fails', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      expect(store.fetchAuditLog('1'), throwsA(isA<ApiException>()));
    });
  });

  group('selection mode', () {
    test('enter/toggle/select-all/clear/exit track the selected set', () async {
      store.reset();
      // Populate the list via a real fetch so selectAll has rows to enumerate.
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([
              _invoiceJson('1'),
              _invoiceJson('2'),
              _invoiceJson('3'),
            ])),
      );
      await store.fetch();
      expect(store.invoices, hasLength(3));

      expect(store.selectionMode, isFalse);

      store.enterSelectionMode('1');
      expect(store.selectionMode, isTrue);
      expect(store.isSelected('1'), isTrue);
      expect(store.selectedCount, 1);

      store.toggleSelected('2');
      expect(store.selectedCount, 2);
      store.toggleSelected('2'); // toggling off
      expect(store.isSelected('2'), isFalse);

      store.selectAll();
      expect(store.selectedCount, 3);

      store.clearSelection();
      expect(store.selectedCount, 0);
      expect(store.selectionMode, isTrue, reason: 'clear keeps the mode on');

      store.exitSelectionMode();
      expect(store.selectionMode, isFalse);
      expect(store.selectedCount, 0);
    });
  });

  group('bulk operations', () {
    test('bulkDeleteSelected posts ids, exits selection, and refetches',
        () async {
      Map<String, dynamic>? sentBody;
      var listCalls = 0;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/bulk/delete')) {
            sentBody = jsonDecode(req.body) as Map<String, dynamic>;
            return http.Response(
              jsonEncode({'deleted': 2, 'skipped': ['3']}),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          listCalls++;
          return _list([]);
        }),
      );

      store
        ..reset()
        ..enterSelectionMode('1')
        ..toggleSelected('2');

      final result = await store.bulkDeleteSelected();

      expect(result, isNotNull);
      expect(result!.count, 2);
      expect(result.skipped, ['3']);
      expect((sentBody!['ids'] as List).toSet(), {'1', '2'});
      expect(store.selectionMode, isFalse, reason: 'exits selection on success');
      expect(listCalls, 1, reason: 'success triggers a refetch');
    });

    test('bulkStatusSelected sends the target status', () async {
      Map<String, dynamic>? sentBody;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/bulk/status')) {
            sentBody = jsonDecode(req.body) as Map<String, dynamic>;
            return http.Response(
              jsonEncode({'updated': 1, 'skipped': []}),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          return _list([]);
        }),
      );

      store
        ..reset()
        ..enterSelectionMode('1');

      final result = await store.bulkStatusSelected('approved');

      expect(result!.count, 1);
      expect(sentBody!['status'], 'approved');
      expect(sentBody!['ids'], ['1']);
    });

    test('bulk action with nothing selected is a no-op (null, no request)',
        () async {
      var calls = 0;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          calls++;
          return _list([]);
        }),
      );

      store.reset();
      expect(await store.bulkDeleteSelected(), isNull);
      expect(await store.bulkStatusSelected('approved'), isNull);
      expect(calls, 0);
    });

    test('failure records the error, returns null, keeps selection', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      store
        ..reset()
        ..enterSelectionMode('1');

      final result = await store.bulkDeleteSelected();

      expect(result, isNull);
      expect(store.error, isNotNull);
      expect(store.selectionMode, isTrue,
          reason: 'a failed bulk op leaves the selection intact to retry');
    });
  });

  group('exportSelected', () {
    test('posts ids + format and returns the bytes + server filename', () async {
      Map<String, dynamic>? sentBody;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST' && req.url.path.endsWith('/bulk/export')) {
            sentBody = jsonDecode(req.body) as Map<String, dynamic>;
            return http.Response(
              'id,vendor\n1,Acme\n',
              200,
              headers: {
                'content-type': 'text/csv',
                'content-disposition':
                    'attachment; filename="invoices-export.csv"',
              },
            );
          }
          return _list([]);
        }),
      );

      store
        ..reset()
        ..enterSelectionMode('1')
        ..toggleSelected('2');

      final result = await store.exportSelected('csv');

      expect(result, isNotNull);
      expect(String.fromCharCodes(result!.bytes), contains('Acme'));
      expect(result.filename, 'invoices-export.csv');
      expect(sentBody!['format'], 'csv');
      expect((sentBody!['ids'] as List).toSet(), {'1', '2'});
      expect(store.selectionMode, isTrue,
          reason: 'export is non-mutating — selection is preserved');
    });

    test('falls back to a default filename when the header is absent', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          return http.Response(
            '<Invoices/>',
            200,
            headers: {'content-type': 'application/xml'},
          );
        }),
      );

      store
        ..reset()
        ..enterSelectionMode('1');

      final result = await store.exportSelected('xml');

      expect(result!.filename, 'invoices-export.xml');
    });

    test('nothing selected is a no-op (null, no request)', () async {
      var calls = 0;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          calls++;
          return _list([]);
        }),
      );

      store.reset();
      expect(await store.exportSelected('csv'), isNull);
      expect(calls, 0);
    });

    test('failure records the error and returns null', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      store
        ..reset()
        ..enterSelectionMode('1');

      final result = await store.exportSelected('csv');

      expect(result, isNull);
      expect(store.error, isNotNull);
    });
  });
}
