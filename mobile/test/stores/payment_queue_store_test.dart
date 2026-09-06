import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:feohledger_mobile/api/api_client.dart';
import 'package:feohledger_mobile/models/payment.dart';
import 'package:feohledger_mobile/services/offline_store.dart';
import 'package:feohledger_mobile/stores/payment_queue_store.dart';

Map<String, dynamic> _queueItem(String id, {double amount = 100}) => {
      'id': id,
      'invoice_number': 'INV-$id',
      'vendor_name': 'Vendor $id',
      'amount': amount,
      'currency': 'USD',
      'due_date': '2026-02-01',
      'status': 'approved',
      'is_overdue': false,
      'discount_eligible': false,
    };

const _summary = {
  'total_paid': 1000.0,
  'total_pending': 200.0,
  'payment_count': 5,
  'total_rebates': 12.0,
  'queue_count': 2,
};

http.Response _json(Object body, [int status = 200]) => http.Response(
      jsonEncode(body),
      status,
      headers: {'content-type': 'application/json'},
    );

void main() {
  final store = PaymentQueueStore.instance;

  setUpAll(() async {
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    PaymentQueueStore.instance.reset();
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
  });

  // Route helper: serves queue + summary (+ optional run endpoints).
  MockClient makeClient({
    List<Map<String, dynamic>>? queue,
    Map<String, dynamic>? summary,
    http.Response Function(http.Request req)? onPost,
  }) {
    return MockClient((req) async {
      if (req.method == 'POST') {
        if (onPost != null) return onPost(req);
        return _json({'message': 'ok'});
      }
      final path = req.url.path;
      if (path.endsWith('/payments/queue')) {
        return _json({
          'items': queue ?? [_queueItem('1')],
          'total': (queue ?? [_queueItem('1')]).length,
          'total_amount': 100.0,
          'total_savings': 0.0,
        });
      }
      if (path.endsWith('/payments/summary')) {
        return _json(summary ?? _summary);
      }
      if (path.contains('/payments/runs')) {
        return _json({'items': [], 'total': 0, 'page': 1});
      }
      return _json({});
    });
  }

  group('fetch', () {
    test('loads queue + summary together', () async {
      ApiClient().debugConfigure(
        client: makeClient(queue: [_queueItem('1'), _queueItem('2')]),
      );

      await store.fetch();

      expect(store.queue, hasLength(2));
      expect(store.summary, isNotNull);
      expect(store.summary!.queueCount, 2);
      expect(store.error, isNull);
      expect(store.fromCache, isFalse);
    });

    test('drops a selection for an invoice that left the queue', () async {
      ApiClient().debugConfigure(client: makeClient(queue: [_queueItem('1')]));
      await store.fetch();
      store.toggleSelection('1');
      expect(store.isSelected('1'), isTrue);

      // Next fetch returns an empty queue — the stale selection is cleared.
      ApiClient().debugConfigure(client: makeClient(queue: []));
      await store.fetch();

      expect(store.isSelected('1'), isFalse);
      expect(store.hasSelection, isFalse);
    });

    test('falls back to the cached queue when the network fails', () async {
      ApiClient().debugConfigure(client: makeClient(queue: [_queueItem('1')]));
      await store.fetch();
      expect(store.fromCache, isFalse);

      ApiClient().debugConfigure(
        client: MockClient((req) async => throw Exception('offline')),
      );
      await store.fetch();

      expect(store.queue, hasLength(1));
      expect(store.fromCache, isTrue);
    });
  });

  group('selection', () {
    test('toggle adds with ACH default, toggle again removes', () {
      store.toggleSelection('a');
      expect(store.isSelected('a'), isTrue);
      expect(store.methodFor('a'), PaymentMethod.ach);
      expect(store.selectedCount, 1);

      store.toggleSelection('a');
      expect(store.isSelected('a'), isFalse);
      expect(store.selectedCount, 0);
    });

    test('setMethod implicitly selects and records the method', () {
      store.setMethod('b', PaymentMethod.wire);
      expect(store.isSelected('b'), isTrue);
      expect(store.methodFor('b'), PaymentMethod.wire);
    });

    test('clearSelection empties the map', () {
      store.toggleSelection('a');
      store.toggleSelection('b');
      store.clearSelection();
      expect(store.hasSelection, isFalse);
    });
  });

  group('createRunFromSelection', () {
    test('posts the selection, clears it, and returns the server message',
        () async {
      http.Request? captured;
      ApiClient().debugConfigure(
        client: makeClient(
          queue: [_queueItem('1')],
          onPost: (req) {
            if (req.url.path.endsWith('/payments/runs')) {
              captured = req;
              return _json({
                'id': 'run1',
                'status': 'draft',
                'requires_cfo_approval': false,
                'message': 'Payment run created with 2 payments',
              });
            }
            return _json({'items': [], 'total': 0});
          },
        ),
      );

      store.setMethod('1', PaymentMethod.wire);
      store.setMethod('2', PaymentMethod.check);

      final message = await store.createRunFromSelection();

      expect(message, contains('Payment run created'));
      expect(store.hasSelection, isFalse, reason: 'selection clears on success');

      // The POST body carries both invoices with their chosen methods.
      final body = jsonDecode(captured!.body) as Map<String, dynamic>;
      final items = body['items'] as List;
      expect(items, hasLength(2));
      final byInvoice = {
        for (final i in items) i['invoice_id']: i['method'],
      };
      expect(byInvoice['1'], 'wire');
      expect(byInvoice['2'], 'check');
    });

    test('returns null and keeps the selection when nothing is selected',
        () async {
      ApiClient().debugConfigure(client: makeClient());
      final message = await store.createRunFromSelection();
      expect(message, isNull);
    });

    test('returns null + records error on failure', () async {
      ApiClient().debugConfigure(
        client: makeClient(
          onPost: (req) {
            if (req.url.path.endsWith('/payments/runs')) {
              return http.Response('boom', 500);
            }
            return _json({});
          },
        ),
      );

      store.toggleSelection('1');
      final message = await store.createRunFromSelection();

      expect(message, isNull);
      expect(store.error, isNotNull);
      // Selection is preserved so the user can retry.
      expect(store.hasSelection, isTrue);
    });
  });

  group('run lifecycle', () {
    test('executeRun posts to /execute and returns the message', () async {
      var executeCalls = 0;
      ApiClient().debugConfigure(
        client: makeClient(
          onPost: (req) {
            if (req.url.path.endsWith('/execute')) {
              executeCalls++;
              return _json({'status': 'completed', 'message': 'Run executed'});
            }
            return _json({'items': [], 'total': 0});
          },
        ),
      );

      final message = await store.executeRun('run1');

      expect(executeCalls, 1);
      expect(message, contains('executed'));
    });

    test('approveRun posts to /approve and returns the message', () async {
      final approvePaths = <String>[];
      ApiClient().debugConfigure(
        client: makeClient(
          onPost: (req) {
            if (req.url.path.endsWith('/approve')) {
              approvePaths.add(req.url.path);
              return _json({
                'id': 'run1',
                'status': 'draft',
                'cfo_approved_by': 'u1',
                'cfo_approved_at': '2026-01-11T09:00:00',
                'message': 'Run approved by CFO',
              });
            }
            return _json({'items': [], 'total': 0});
          },
        ),
      );

      final message = await store.approveRun('run1');

      expect(approvePaths, ['/api/payments/runs/run1/approve']);
      expect(message, 'Run approved by CFO');
    });

    test('approveRun surfaces the server sentence on a refusal', () async {
      ApiClient().debugConfigure(
        client: makeClient(
          onPost: (req) {
            if (req.url.path.endsWith('/approve')) {
              return _json(
                {'detail': 'Run is already CFO-approved'},
                403,
              );
            }
            return _json({'items': [], 'total': 0});
          },
        ),
      );

      expect(await store.approveRun('run1'), isNull);
      expect(store.error, 'Run is already CFO-approved');
    });

    test('cancelRun posts to /cancel and returns the message', () async {
      var cancelCalls = 0;
      ApiClient().debugConfigure(
        client: makeClient(
          onPost: (req) {
            if (req.url.path.endsWith('/cancel')) {
              cancelCalls++;
              return _json({'status': 'cancelled', 'message': 'Run cancelled'});
            }
            return _json({'items': [], 'total': 0});
          },
        ),
      );

      final message = await store.cancelRun('run1');

      expect(cancelCalls, 1);
      expect(message, contains('cancelled'));
    });
  });
}
