import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/screens/invoices_screen.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/widgets/invoice_list_tile.dart';

http.Response _list(List<Map<String, dynamic>> items) => http.Response(
      jsonEncode({'invoices': items}),
      200,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _invoiceJson(
  String id, {
  String status = 'pending',
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

/// Pumps fixed bounded frames until [finder] is present (or the budget is
/// exhausted). Never use pumpAndSettle on this screen — a pending fetch leaves
/// a CircularProgressIndicator animating forever.
Future<void> _pumpUntil(WidgetTester tester, Finder finder) async {
  for (var i = 0; i < 20 && finder.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  final store = InvoiceStore.instance;

  setUpAll(() async {
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    InvoiceStore.instance.debugReset();
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
    // Stores are singletons — reset the filter so chip-selection tests start
    // from the 'All' (null) baseline regardless of test ordering.
    store.setStatusFilter(null);
  });

  testWidgets('shows a loading spinner while the initial fetch is in flight',
      (tester) async {
    // Hold the response open so the fetch never resolves during this test.
    final gate = Completer<http.Response>();
    ApiClient().debugConfigure(
      client: MockClient((req) async => gate.future),
    );

    await tester.pumpWidget(const MaterialApp(home: InvoicesScreen()));
    // One pump runs the post-frame fetch; the store flips loading=true.
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    // Release the fetch and let the spinner leave the tree so no Timer/ticker
    // remains pending after disposal.
    gate.complete(_list([]));
    await _pumpUntil(tester, find.text('No invoices found'));
  });

  testWidgets('renders one InvoiceListTile per invoice once loaded',
      (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([
            _invoiceJson('1', vendor: 'Acme Corp'),
            _invoiceJson('2', vendor: 'Globex'),
          ])),
    );

    await tester.pumpWidget(const MaterialApp(home: InvoicesScreen()));
    await _pumpUntil(tester, find.byType(InvoiceListTile));

    expect(find.byType(InvoiceListTile), findsNWidgets(2));
    expect(find.text('Acme Corp'), findsOneWidget);
    expect(find.text('Globex'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('renders the empty state when the list comes back empty',
      (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([])),
    );

    await tester.pumpWidget(const MaterialApp(home: InvoicesScreen()));
    await _pumpUntil(tester, find.text('No invoices found'));

    expect(find.text('No invoices found'), findsOneWidget);
    expect(find.byType(InvoiceListTile), findsNothing);
  });

  testWidgets('falls back to the empty state on a network error with no cache',
      (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => throw Exception('offline')),
    );

    await tester.pumpWidget(const MaterialApp(home: InvoicesScreen()));
    await _pumpUntil(tester, find.text('No invoices found'));

    // No cached rows exist, so the store ends with an empty list + error set,
    // and the screen shows its empty placeholder (not a spinner).
    expect(find.text('No invoices found'), findsOneWidget);
    expect(store.error, isNotNull);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('renders the search bar, status filter chips and camera action',
      (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([])),
    );

    await tester.pumpWidget(const MaterialApp(home: InvoicesScreen()));
    await _pumpUntil(tester, find.text('No invoices found'));

    expect(find.byType(SearchBar), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'All'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Pending'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Approved'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Paid'), findsOneWidget);
    expect(find.byIcon(Icons.camera_alt), findsOneWidget);
  });

  testWidgets('the "All" chip is selected by default', (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([])),
    );

    await tester.pumpWidget(const MaterialApp(home: InvoicesScreen()));
    await _pumpUntil(tester, find.text('No invoices found'));

    final allChip = tester.widget<FilterChip>(
      find.widgetWithText(FilterChip, 'All'),
    );
    expect(allChip.selected, isTrue);
    expect(store.statusFilter, isNull);
  });

  testWidgets('tapping a status chip carries that status into the request',
      (tester) async {
    String? lastStatus;
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        lastStatus = req.url.queryParameters['status'];
        return _list([]);
      }),
    );

    await tester.pumpWidget(const MaterialApp(home: InvoicesScreen()));
    await _pumpUntil(tester, find.text('No invoices found'));

    await tester.tap(find.widgetWithText(FilterChip, 'Approved'));
    await _pumpUntil(tester, find.byType(InvoiceListTile));
    // Empty result — give the refetch a couple of frames to land.
    await tester.pump(const Duration(milliseconds: 50));

    expect(store.statusFilter, 'approved');
    expect(lastStatus, 'approved');
  });

  testWidgets('typing in the search box drives the store search filter',
      (tester) async {
    String? lastSearch;
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        lastSearch = req.url.queryParameters['search'];
        return _list([]);
      }),
    );

    await tester.pumpWidget(const MaterialApp(home: InvoicesScreen()));
    await _pumpUntil(tester, find.text('No invoices found'));

    await tester.enterText(find.byType(SearchBar), 'acme');
    await tester.pump(const Duration(milliseconds: 50));

    expect(lastSearch, 'acme');
  });

  testWidgets('serves cached rows when the network fails after a prior load',
      (tester) async {
    // 1) Prime the cache + store via a successful fetch.
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([_invoiceJson('1')])),
    );
    await store.fetch();
    expect(store.fromCache, isFalse);

    // 2) Network now down; mounting the screen triggers a fetch that falls
    // back to the cache for the same (null filter, no search) key.
    ApiClient().debugConfigure(
      client: MockClient((req) async => throw Exception('offline')),
    );

    await tester.pumpWidget(const MaterialApp(home: InvoicesScreen()));
    await _pumpUntil(tester, find.byType(InvoiceListTile));

    expect(find.byType(InvoiceListTile), findsOneWidget);
    expect(store.fromCache, isTrue);
  });
}
