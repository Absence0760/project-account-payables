import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:feohledger_mobile/api/api_client.dart';
import 'package:feohledger_mobile/l10n/gen/app_localizations.dart';
import 'package:feohledger_mobile/screens/payment_queue_screen.dart';
import 'package:feohledger_mobile/services/offline_store.dart';
import 'package:feohledger_mobile/stores/auth_store.dart';
import 'package:feohledger_mobile/stores/payment_queue_store.dart';

/// Wraps a screen in a MaterialApp carrying the localization delegates so
/// `AppLocalizations.of(context)` resolves (defaults to English).
Widget _localized(Widget home) => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: home,
    );

http.Response _json(Object body, [int status = 200]) => http.Response(
      jsonEncode(body),
      status,
      headers: {'content-type': 'application/json'},
    );

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

Map<String, dynamic> _me(List<String> roles) => {
      'id': 'u1',
      'email': 'demo@acme.com',
      'full_name': 'Demo User',
      'organization_id': 'org1',
      'roles': roles,
    };

http.Response _queueResponse(List<Map<String, dynamic>> items) => _json({
      'items': items,
      'total': items.length,
      'total_amount': 0,
      'total_savings': 0,
    });

MockClient _screenClient({
  List<Map<String, dynamic>>? queue,
  http.Response Function(http.Request req)? onPost,
}) {
  return MockClient((req) async {
    if (req.method == 'POST') {
      return onPost?.call(req) ?? _json({'message': 'ok'});
    }
    final path = req.url.path;
    if (path.endsWith('/payments/queue')) {
      return _queueResponse(queue ?? [_queueItem('1')]);
    }
    if (path.endsWith('/payments/summary')) return _json(_summary);
    if (path.contains('/payments/runs')) {
      return _json({'items': <Map<String, dynamic>>[], 'total': 0});
    }
    return _json({});
  });
}

Future<void> _pumpUntil(WidgetTester tester, Finder finder) async {
  for (var i = 0; i < 25 && finder.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

Future<void> _pumpUntilTrue(WidgetTester tester, bool Function() c) async {
  for (var i = 0; i < 25 && !c(); i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    PaymentQueueStore.instance.reset();
    await OfflineStore.instance.clear();
    FlutterSecureStorage.setMockInitialValues({});
    ApiClient().debugConfigure();
  });

  Future<void> loginThen(List<String> roles, MockClient screenClient) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.url.path == '/api/auth/login') {
          return _json({'access_token': 'tok'});
        }
        return _json(_me(roles));
      }),
    );
    await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');
    ApiClient().debugConfigure(client: screenClient);
  }

  testWidgets('renders the summary KPI cards + queue rows once loaded',
      (tester) async {
    await loginThen(
      ['ap_manager'],
      _screenClient(queue: [_queueItem('1'), _queueItem('2')]),
    );

    await tester.pumpWidget(_localized(const PaymentQueueScreen()));
    await _pumpUntil(tester, find.text('Total Paid'));

    expect(find.text('Total Paid'), findsOneWidget);
    expect(find.text('Card Rebates'), findsOneWidget);
    // Two queue rows -> two vendor names rendered.
    expect(find.text('Vendor 1'), findsOneWidget);
    expect(find.text('Vendor 2'), findsOneWidget);
  });

  testWidgets('selecting a row reveals the Create Run bar', (tester) async {
    await loginThen(['ap_manager'], _screenClient(queue: [_queueItem('1')]));

    await tester.pumpWidget(_localized(const PaymentQueueScreen()));
    await _pumpUntil(tester, find.text('Vendor 1'));

    expect(find.text('Create Run'), findsNothing);

    await tester.tap(find.byType(Checkbox).first);
    await tester.pump();

    expect(find.text('Create Run'), findsOneWidget);
    expect(find.textContaining('1 invoice selected'), findsOneWidget);
  });

  testWidgets('Create Run POSTs the selection to /payments/runs',
      (tester) async {
    http.Request? captured;
    await loginThen(
      ['ap_manager'],
      _screenClient(
        queue: [_queueItem('1')],
        onPost: (req) {
          if (req.url.path.endsWith('/payments/runs')) {
            captured = req;
            return _json({
              'id': 'run1',
              'status': 'draft',
              'requires_cfo_approval': false,
              'message': 'Payment run created',
            });
          }
          return _json({'items': [], 'total': 0});
        },
      ),
    );

    await tester.pumpWidget(_localized(const PaymentQueueScreen()));
    await _pumpUntil(tester, find.text('Vendor 1'));

    await tester.tap(find.byType(Checkbox).first);
    await tester.pump();
    await tester.tap(find.text('Create Run'));
    await _pumpUntilTrue(tester, () => captured != null);

    expect(captured, isNotNull);
    final body = jsonDecode(captured!.body) as Map<String, dynamic>;
    expect((body['items'] as List).first['invoice_id'], '1');
  });

  testWidgets('cfo read-only context: no checkbox column when role cannot '
      'manage payments is still managed (cfo CAN manage)', (tester) async {
    // CFO can manage payments per the backend gate, so the checkbox IS shown.
    await loginThen(['cfo'], _screenClient(queue: [_queueItem('1')]));

    await tester.pumpWidget(_localized(const PaymentQueueScreen()));
    await _pumpUntil(tester, find.text('Vendor 1'));

    expect(find.byType(Checkbox), findsOneWidget);
  });

  testWidgets('empty queue renders the empty state', (tester) async {
    await loginThen(['ap_manager'], _screenClient(queue: []));

    await tester.pumpWidget(_localized(const PaymentQueueScreen()));
    await _pumpUntil(tester, find.text('No invoices awaiting payment'));

    expect(find.text('No invoices awaiting payment'), findsOneWidget);
  });
}
