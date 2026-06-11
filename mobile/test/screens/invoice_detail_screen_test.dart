import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/screens/invoice_detail_screen.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/auth_store.dart';
import 'package:ap_mobile/stores/invoice_store.dart';

http.Response _json(Object body, [int status = 200]) => http.Response(
      jsonEncode(body),
      status,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _meBody(List<String> roles) => {
      'id': 'u1',
      'email': 'demo@acme.com',
      'full_name': 'Demo User',
      'organization_id': 'org1',
      'roles': roles,
    };

Map<String, dynamic> _invoiceJson(
  String id, {
  String status = 'ready_for_review',
  String? vendor = 'Acme Corp',
  num? amount = 1234.56,
  String? invoiceNumber = 'INV-001',
  String? poNumber,
  String? description,
  String? fileUrl,
}) =>
    {
      'id': id,
      'invoice_number': invoiceNumber,
      'vendor_name': vendor,
      'amount': amount,
      'currency': 'USD',
      'status': status,
      'po_number': poNumber,
      'description': description,
      'file_url': fileUrl,
      'created_at': '2026-01-01T12:00:00',
    };

/// Pump bounded frames until [finder] is present (or attempts run out) so the
/// screen's fetch resolves and the loading spinner leaves the tree. Never use
/// pumpAndSettle here — the CircularProgressIndicator animates forever.
Future<void> _pumpUntil(WidgetTester tester, Finder finder) async {
  for (var i = 0; i < 30 && finder.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

/// Install [client] and drive a real login so the AuthStore is populated (the
/// roles come from the client's GET /api/auth/me handler). debugConfigure
/// resets the session, so login must run after the client is installed.
Future<void> _arrange(MockClient client) async {
  ApiClient().debugConfigure(client: client);
  final ok = await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');
  expect(ok, isTrue, reason: 'login fixture should succeed');
}

/// A MockClient covering auth + a single invoice detail GET returning [invoice].
/// Optional [onApprove] / [onReject] override those POSTs.
MockClient _detailClient(
  Map<String, dynamic> invoice, {
  http.Response Function()? onGet,
  http.Response Function(http.Request req)? onApprove,
  http.Response Function(http.Request req)? onReject,
}) {
  return MockClient((req) async {
    final path = req.url.path;
    if (req.method == 'POST' && path == '/api/auth/login') {
      return _json({'access_token': 'tok-123'});
    }
    if (req.method == 'GET' && path == '/api/auth/me') {
      return _json(_meBody(['admin']));
    }
    if (req.method == 'POST' && path.endsWith('/approve')) {
      return onApprove?.call(req) ?? _json(invoice);
    }
    if (req.method == 'POST' && path.endsWith('/reject')) {
      return onReject?.call(req) ?? _json(invoice);
    }
    // InvoiceStore.approve/reject refetch the list after the POST.
    if (req.method == 'GET' && path == '/api/invoices') {
      return _json({'invoices': <Map<String, dynamic>>[]});
    }
    if (req.method == 'GET' && path.startsWith('/api/invoices/')) {
      return onGet?.call() ?? _json(invoice);
    }
    return http.Response('not found', 404);
  });
}

void main() {
  setUpAll(() async {
    // InvoiceStore.approve/reject touch the OfflineStore via a refetch.
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    InvoiceStore.instance.debugReset();
    FlutterSecureStorage.setMockInitialValues({});
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
  });

  testWidgets('shows a loading spinner before the fetch resolves',
      (tester) async {
    // Gate the detail GET on a Completer so we can assert the spinner while the
    // fetch is in flight, then resolve it so no async work outlives the test.
    final gate = Completer<http.Response>();
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.url.path == '/api/auth/login') {
          return _json({'access_token': 'tok-123'});
        }
        if (req.url.path == '/api/auth/me') {
          return _json(_meBody(['admin']));
        }
        return gate.future; // detail fetch blocks here
      }),
    );
    await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    // Release the fetch and let the screen reach its loaded state so nothing is
    // left mid-load at teardown.
    gate.complete(_json(_invoiceJson('1')));
    await _pumpUntil(tester, find.text('Acme Corp'));
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('renders invoice detail fields once the fetch resolves',
      (tester) async {
    await _arrange(
      _detailClient(_invoiceJson(
        '1',
        vendor: 'Globex LLC',
        poNumber: 'PO-42',
        description: 'Quarterly cloud bill',
      )),
    );

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Globex LLC'));

    expect(find.text('Globex LLC'), findsOneWidget);
    expect(find.text('INV-001'), findsOneWidget);
    expect(find.text('PO-42'), findsOneWidget);
    expect(find.text('Quarterly cloud bill'), findsOneWidget);
    expect(find.text(r'$1,234.56'), findsOneWidget);
    // No spinner remains.
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('falls back to "Unknown Vendor" when vendor_name is null',
      (tester) async {
    await _arrange(
      _detailClient(_invoiceJson('1', vendor: null)),
    );

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Unknown Vendor'));

    expect(find.text('Unknown Vendor'), findsOneWidget);
  });

  testWidgets('renders an error message when the fetch fails', (tester) async {
    final client = MockClient((req) async {
      final path = req.url.path;
      if (path == '/api/auth/login') return _json({'access_token': 'tok-123'});
      if (path == '/api/auth/me') return _json(_meBody(['admin']));
      return http.Response('boom', 500);
    });
    ApiClient().debugConfigure(client: client);
    await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.textContaining('Error:'));

    expect(find.textContaining('Error:'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('shows Approve/Reject actions for an actionable invoice when the '
      'user can approve', (tester) async {
    await _arrange(
      _detailClient(_invoiceJson('1', status: 'ready_for_review')),
    );

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Approve'));

    expect(find.widgetWithText(FilledButton, 'Approve'), findsOneWidget);
    expect(find.widgetWithText(OutlinedButton, 'Reject'), findsOneWidget);
  });

  testWidgets('hides the action bar when the invoice is not actionable',
      (tester) async {
    await _arrange(
      _detailClient(_invoiceJson('1', status: 'approved')),
    );

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Acme Corp'));

    expect(find.widgetWithText(FilledButton, 'Approve'), findsNothing);
    expect(find.widgetWithText(OutlinedButton, 'Reject'), findsNothing);
  });

  testWidgets('hides the action bar for a clerk even on an actionable invoice',
      (tester) async {
    // ap_clerk cannot approve -> action bar suppressed by canApprove gate.
    final client = MockClient((req) async {
      final path = req.url.path;
      if (path == '/api/auth/login') return _json({'access_token': 'tok-123'});
      if (path == '/api/auth/me') return _json(_meBody(['ap_clerk']));
      if (req.method == 'GET' && path.startsWith('/api/invoices/')) {
        return _json(_invoiceJson('1', status: 'ready_for_review'));
      }
      return http.Response('not found', 404);
    });
    ApiClient().debugConfigure(client: client);
    await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Acme Corp'));

    expect(find.text('Acme Corp'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Approve'), findsNothing);
    expect(find.widgetWithText(OutlinedButton, 'Reject'), findsNothing);
  });

  testWidgets('tapping Approve posts approval and shows a confirmation snackbar',
      (tester) async {
    var approveCalls = 0;
    final client = _detailClient(
      _invoiceJson('1', status: 'ready_for_review'),
      onApprove: (req) {
        approveCalls++;
        return _json(_invoiceJson('1', status: 'approved'));
      },
      onGet: () => approveCalls == 0
          ? _json(_invoiceJson('1', status: 'ready_for_review'))
          : _json(_invoiceJson('1', status: 'approved')),
    );
    ApiClient().debugConfigure(client: client);
    await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Approve'));

    await tester.tap(find.widgetWithText(FilledButton, 'Approve'));
    await _pumpUntil(tester, find.text('Invoice approved'));

    expect(approveCalls, 1);
    expect(find.text('Invoice approved'), findsOneWidget);
  });

  testWidgets('a double-tap on Approve only fires one approval POST',
      (tester) async {
    var approveCalls = 0;
    final gate = Completer<http.Response>();
    final client = MockClient((req) async {
      final path = req.url.path;
      if (req.method == 'POST' && path == '/api/auth/login') {
        return _json({'access_token': 'tok-123'});
      }
      if (req.method == 'GET' && path == '/api/auth/me') {
        return _json(_meBody(['admin']));
      }
      if (req.method == 'POST' && path.endsWith('/approve')) {
        approveCalls++;
        return gate.future; // hold the approval in flight
      }
      if (req.method == 'GET' && path == '/api/invoices') {
        return _json({'invoices': <Map<String, dynamic>>[]});
      }
      if (req.method == 'GET' && path.startsWith('/api/invoices/')) {
        return _json(_invoiceJson('1',
            status: approveCalls == 0 ? 'ready_for_review' : 'approved'));
      }
      return http.Response('not found', 404);
    });
    ApiClient().debugConfigure(client: client);
    await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Approve'));

    final approveBtn = find.widgetWithText(FilledButton, 'Approve');
    await tester.tap(approveBtn);
    await tester.pump(); // _submitting=true → button disabled
    // Second tap while the first is in flight must be ignored.
    await tester.tap(approveBtn, warnIfMissed: false);
    await tester.pump();

    expect(approveCalls, 1, reason: 'double-tap must not double-POST');

    // Release the held approval so nothing outlives the test.
    gate.complete(_json(_invoiceJson('1', status: 'approved')));
    await _pumpUntil(tester, find.text('Invoice approved'));
  });

  testWidgets('shows an error snackbar when the approval fails',
      (tester) async {
    final client = _detailClient(
      _invoiceJson('1', status: 'ready_for_review'),
      onApprove: (req) => http.Response('boom', 500),
    );
    ApiClient().debugConfigure(client: client);
    await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Approve'));

    await tester.tap(find.widgetWithText(FilledButton, 'Approve'));
    await _pumpUntil(tester, find.textContaining('Could not approve'));

    // The user gets explicit failure feedback instead of a silent no-op.
    expect(find.textContaining('Could not approve'), findsOneWidget);
  });

  testWidgets('tapping Reject opens the reason dialog with Cancel/Reject',
      (tester) async {
    await _arrange(
      _detailClient(_invoiceJson('1', status: 'ready_for_review')),
    );

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Reject'));

    await tester.tap(find.widgetWithText(OutlinedButton, 'Reject'));
    await tester.pumpAndSettle();

    expect(find.text('Reject Invoice'), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget);
    // The reason field's labelText renders as a Text node inside the dialog.
    expect(find.text('Reason'), findsOneWidget);
    expect(find.widgetWithText(TextButton, 'Cancel'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Reject'), findsOneWidget);
  });

  testWidgets('submitting a reject reason posts it and shows a snackbar',
      (tester) async {
    String? sentReason;
    var rejectCalls = 0;
    final client = _detailClient(
      _invoiceJson('1', status: 'ready_for_review'),
      onReject: (req) {
        rejectCalls++;
        sentReason = (jsonDecode(req.body) as Map)['reason'] as String?;
        return _json(_invoiceJson('1', status: 'rejected'));
      },
      onGet: () => rejectCalls == 0
          ? _json(_invoiceJson('1', status: 'ready_for_review'))
          : _json(_invoiceJson('1', status: 'rejected')),
    );
    ApiClient().debugConfigure(client: client);
    await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Reject'));

    await tester.tap(find.widgetWithText(OutlinedButton, 'Reject'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'Wrong amount');
    await tester.tap(find.widgetWithText(FilledButton, 'Reject'));
    await _pumpUntil(tester, find.text('Invoice rejected'));

    expect(rejectCalls, 1);
    expect(sentReason, 'Wrong amount');
    expect(find.text('Invoice rejected'), findsOneWidget);
  });

  testWidgets('cancelling the reject dialog posts nothing', (tester) async {
    var rejectCalls = 0;
    final client = _detailClient(
      _invoiceJson('1', status: 'ready_for_review'),
      onReject: (req) {
        rejectCalls++;
        return _json(_invoiceJson('1', status: 'rejected'));
      },
    );
    ApiClient().debugConfigure(client: client);
    await AuthStore.instance.login('demo@acme.com', 'demo', 'acme');

    await tester.pumpWidget(
      const MaterialApp(home: InvoiceDetailScreen(invoiceId: '1')),
    );
    await _pumpUntil(tester, find.text('Reject'));

    await tester.tap(find.widgetWithText(OutlinedButton, 'Reject'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextButton, 'Cancel'));
    await tester.pumpAndSettle();

    expect(find.text('Reject Invoice'), findsNothing);
    expect(rejectCalls, 0);
  });
}
