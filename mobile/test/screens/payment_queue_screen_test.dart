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

/// One row of `GET /api/payments/runs/`, mirroring `PaymentRunResponse` in
/// `backend/app/schemas/payment.py` field for field. Hand-written fixtures
/// carrying only the keys the model reads are how the CFO-approval gate stayed
/// "covered" while the response schema declared no such field.
Map<String, dynamic> _runResponse({
  String id = 'run1',
  String status = 'draft',
  double totalAmount = 5000.0,
  int paymentCount = 2,
  bool requiresCfoApproval = false,
  String? cfoApprovedAt,
}) =>
    {
      'id': id,
      'status': status,
      'total_amount': totalAmount,
      'initiated_by': 'u1',
      'executed_at': null,
      'created_at': '2026-01-10T12:00:00',
      'payment_count': paymentCount,
      'payments_completed': 0,
      'payments_failed': 0,
      'payments_in_flight': 0,
      'payments_pending': paymentCount,
      'requires_cfo_approval': requiresCfoApproval,
      'cfo_approved_at': cfoApprovedAt,
    };

MockClient _screenClient({
  List<Map<String, dynamic>>? queue,
  List<Map<String, dynamic>>? runs,
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
      final items = runs ?? <Map<String, dynamic>>[];
      return _json({'items': items, 'total': items.length});
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

  // The CFO-approval gate. `GET /api/payments/runs/` must declare
  // `requires_cfo_approval` / `cfo_approved_at` on `PaymentRunResponse` for
  // this to be reachable at all — without them FastAPI strips the keys, both
  // parse false for every run, and Execute goes to the server only to come
  // back 403.
  group('CFO-approval gate', () {
    Future<void> openRunsTab(WidgetTester tester) async {
      await _pumpUntil(tester, find.text('Runs'));
      await tester.tap(find.text('Runs'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
    }

    testWidgets('a run awaiting sign-off is flagged and Execute never reaches '
        'the server', (tester) async {
      var executeCalls = 0;
      await loginThen(
        ['ap_manager'],
        _screenClient(
          queue: [],
          runs: [_runResponse(requiresCfoApproval: true)],
          onPost: (req) {
            if (req.url.path.endsWith('/execute')) executeCalls++;
            return _json({'message': 'ok'});
          },
        ),
      );

      await tester.pumpWidget(_localized(const PaymentQueueScreen()));
      await openRunsTab(tester);

      expect(find.textContaining('CFO approval required'), findsOneWidget);

      await tester.tap(find.byType(PopupMenuButton<String>));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Execute'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));

      expect(executeCalls, 0, reason: 'the pre-flight gate must short-circuit');
      expect(
        find.text('This run needs CFO approval before it can be executed.'),
        findsOneWidget,
      );
    });

    testWidgets('a signed-off run executes normally', (tester) async {
      var executeCalls = 0;
      await loginThen(
        ['cfo'],
        _screenClient(
          queue: [],
          runs: [
            _runResponse(
              requiresCfoApproval: true,
              cfoApprovedAt: '2026-01-11T09:00:00',
            ),
          ],
          onPost: (req) {
            if (req.url.path.endsWith('/execute')) executeCalls++;
            return _json({'message': 'Payment run executed'});
          },
        ),
      );

      await tester.pumpWidget(_localized(const PaymentQueueScreen()));
      await openRunsTab(tester);

      expect(find.textContaining('CFO approval required'), findsNothing);

      await tester.tap(find.byType(PopupMenuButton<String>));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Execute'));
      await tester.pumpAndSettle();
      // Confirm the "Execute payment run?" dialog.
      await tester.tap(find.widgetWithText(FilledButton, 'Execute'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));

      expect(executeCalls, 1);
    });

    testWidgets('a 403 from the server surfaces its sentence, not raw JSON',
        (tester) async {
      await loginThen(
        ['ap_manager'],
        _screenClient(
          queue: [],
          // The server says sign-off is not required (e.g. the threshold moved
          // after the run was drafted), so the pre-flight gate lets it through
          // and the refusal comes back over the wire.
          runs: [_runResponse()],
          onPost: (req) {
            if (req.url.path.endsWith('/execute')) {
              return _json(
                {'detail': 'This run exceeds the CFO-approval threshold.'},
                403,
              );
            }
            return _json({'message': 'ok'});
          },
        ),
      );

      await tester.pumpWidget(_localized(const PaymentQueueScreen()));
      await openRunsTab(tester);

      await tester.tap(find.byType(PopupMenuButton<String>));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Execute'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Execute'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));

      expect(
        find.text(
          'Failed to execute: This run exceeds the CFO-approval threshold.',
        ),
        findsOneWidget,
      );
      expect(find.textContaining('{"detail"'), findsNothing);
    });
  });
}
