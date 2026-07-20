import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/l10n/gen/app_localizations.dart';
import 'package:ap_mobile/screens/approvals_screen.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/widgets/invoice_list_tile.dart';

/// Wraps the screen in a MaterialApp carrying the localization delegates so the
/// localized `AppLocalizations.of(context)` resolves (defaults to English).
Widget _localized() => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: const ApprovalsScreen(),
    );

http.Response _list(List<Map<String, dynamic>> items) => http.Response(
      jsonEncode({'invoices': items}),
      200,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _invoiceJson(
  String id, {
  String status = 'ready_for_review',
  String vendor = 'Acme Corp',
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

/// Pumps bounded frames until [finder] appears (or the budget runs out), so we
/// never call pumpAndSettle on a screen that may be showing a perpetual spinner.
Future<void> _pumpUntil(
  WidgetTester tester,
  Finder finder, {
  int maxFrames = 30,
}) async {
  for (var i = 0; i < maxFrames && finder.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  final store = InvoiceStore.instance;

  setUpAll(() async {
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    InvoiceStore.instance.reset();
    FlutterSecureStorage.setMockInitialValues({});
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
    // Drain any pending approval the previous test left in the singleton by
    // fetching an empty list through a throwaway mock, so the screen under
    // test starts from an empty store. (The store filters client-side, so the
    // leaky _statusFilter never affects the mocked responses below.)
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([])),
    );
    await store.fetch();
  });

  testWidgets('shows the spinner while the initial fetch is in flight',
      (tester) async {
    final gate = Completer<void>();
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        await gate.future; // hold the fetch open
        return _list([]);
      }),
    );

    await tester.pumpWidget(_localized());
    // Let the post-frame callback fire and the store flip to loading.
    await tester.pump();
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    // Resolve the fetch so no Timer/ticker is left pending at teardown.
    gate.complete();
    await _pumpUntil(tester, find.text('All caught up!'));
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('renders the empty "All caught up!" state when nothing is pending',
      (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([])),
    );

    await tester.pumpWidget(_localized());
    await _pumpUntil(tester, find.text('All caught up!'));

    expect(find.text('All caught up!'), findsOneWidget);
    expect(find.text('No invoices waiting for approval'), findsOneWidget);
    expect(find.byIcon(Icons.check_circle), findsOneWidget);
    expect(find.byType(InvoiceListTile), findsNothing);
  });

  testWidgets('non-pending invoices alone still render the empty state',
      (tester) async {
    // Approved/pending invoices exist but none are ready_for_review.
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([
            _invoiceJson('1', status: 'approved'),
            _invoiceJson('2', status: 'pending'),
          ])),
    );

    await tester.pumpWidget(_localized());
    await _pumpUntil(tester, find.text('All caught up!'));

    expect(find.text('All caught up!'), findsOneWidget);
    expect(find.byType(InvoiceListTile), findsNothing);
  });

  testWidgets('lists only the ready_for_review invoices with a plural count',
      (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([
            _invoiceJson('1', status: 'ready_for_review', vendor: 'Vendor One'),
            _invoiceJson('2', status: 'approved', vendor: 'Vendor Two'),
            _invoiceJson('3', status: 'ready_for_review', vendor: 'Vendor Three'),
          ])),
    );

    await tester.pumpWidget(_localized());
    await _pumpUntil(tester, find.byType(InvoiceListTile));

    // Only the two ready_for_review rows render.
    expect(find.byType(InvoiceListTile), findsNWidgets(2));
    expect(find.text('Vendor One'), findsOneWidget);
    expect(find.text('Vendor Three'), findsOneWidget);
    expect(find.text('Vendor Two'), findsNothing);
    expect(find.text('2 invoices pending'), findsOneWidget);
  });

  testWidgets('uses the singular "1 invoice pending" label for one item',
      (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([_invoiceJson('1')])),
    );

    await tester.pumpWidget(_localized());
    await _pumpUntil(tester, find.byType(InvoiceListTile));

    expect(find.text('1 invoice pending'), findsOneWidget);
    expect(find.text('1 invoices pending'), findsNothing);
  });

  testWidgets('renders the "Pending Approvals" app bar title', (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([])),
    );

    await tester.pumpWidget(_localized());
    await _pumpUntil(tester, find.text('All caught up!'));

    expect(
      find.widgetWithText(AppBar, 'Pending Approvals'),
      findsOneWidget,
    );
  });

  testWidgets('swipe start-to-end approves the invoice via the store',
      (tester) async {
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
        // After approval, the refetch returns an empty pending list.
        return _list(approveCalls == 0 ? [_invoiceJson('1')] : []);
      }),
    );

    await tester.pumpWidget(_localized());
    await _pumpUntil(tester, find.byType(InvoiceListTile));
    expect(find.byType(InvoiceListTile), findsOneWidget);

    // Swipe left-to-right (startToEnd) triggers confirmDismiss -> approve().
    await tester.drag(
      find.byType(InvoiceListTile),
      const Offset(600, 0),
    );
    await _pumpUntil(tester, find.text('All caught up!'));

    expect(approveCalls, 1, reason: 'swipe-right should approve once');
    expect(
      listCalls,
      greaterThanOrEqualTo(2),
      reason: 'initial fetch + refetch after approve',
    );
    expect(find.text('All caught up!'), findsOneWidget);
  });

  testWidgets('swipe end-to-start does not approve (reject needs a reason)',
      (tester) async {
    var approveCalls = 0;
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.method == 'POST') {
          approveCalls++;
          return http.Response('{}', 200,
              headers: {'content-type': 'application/json'});
        }
        return _list([_invoiceJson('1')]);
      }),
    );

    await tester.pumpWidget(_localized());
    await _pumpUntil(tester, find.byType(InvoiceListTile));

    // Swipe right-to-left (endToStart) — confirmDismiss returns false.
    await tester.drag(
      find.byType(InvoiceListTile),
      const Offset(-600, 0),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    expect(approveCalls, 0, reason: 'reject swipe must not call approve');
    // The row snaps back — still present and pending.
    expect(find.byType(InvoiceListTile), findsOneWidget);
  });
}
