import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/l10n/gen/app_localizations.dart';
import 'package:ap_mobile/screens/exceptions_screen.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/exception_store.dart';
import 'package:ap_mobile/widgets/exception_list_tile.dart';

Widget _localized(Widget home) => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: home,
    );

http.Response _list(List<Map<String, dynamic>> items) => http.Response(
      jsonEncode({'items': items, 'total': items.length, 'page': 1}),
      200,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _exceptionJson(
  String id, {
  String status = 'open',
  String type = 'duplicate',
  String typeLabel = 'Duplicate Invoice',
}) =>
    {
      'id': id,
      'invoice_id': 'inv-$id',
      'invoice_number': 'INV-$id',
      'vendor_name': 'Acme Corp',
      'amount': 250,
      'exception_type': type,
      'type_label': typeLabel,
      'severity': 'error',
      'status': status,
      'is_overdue': false,
      'created_at': '2026-01-01T12:00:00',
    };

/// Pumps fixed bounded frames until [finder] is present (or the budget is
/// exhausted). Never pumpAndSettle — a pending fetch leaves a spinner
/// animating forever.
Future<void> _pumpUntil(WidgetTester tester, Finder finder) async {
  for (var i = 0; i < 20 && finder.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

/// Pumps bounded frames until [condition] holds — used to wait on the real
/// signal that an async store action (POST then refetch) has landed, rather
/// than guessing a fixed delay.
Future<void> _pumpUntilTrue(WidgetTester tester, bool Function() condition) async {
  for (var i = 0; i < 20 && !condition(); i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  final store = ExceptionStore.instance;

  setUpAll(() async {
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    ExceptionStore.instance.debugReset();
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
    store.setStatusFilter(null);
  });

  testWidgets('renders one ExceptionListTile per exception once loaded',
      (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([
            _exceptionJson('1', typeLabel: 'Duplicate Invoice'),
            _exceptionJson('2', type: 'po_mismatch', typeLabel: 'PO Mismatch'),
          ])),
    );

    await tester.pumpWidget(_localized(const ExceptionsScreen()));
    await _pumpUntil(tester, find.byType(ExceptionListTile));

    expect(find.byType(ExceptionListTile), findsNWidgets(2));
    expect(find.text('Duplicate Invoice'), findsOneWidget);
    expect(find.text('PO Mismatch'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('renders the empty state when the queue is clear', (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([])),
    );

    await tester.pumpWidget(_localized(const ExceptionsScreen()));
    await _pumpUntil(tester, find.text('No exceptions'));

    expect(find.text('No exceptions'), findsOneWidget);
    expect(find.byType(ExceptionListTile), findsNothing);
  });

  testWidgets('renders the status filter chips', (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([])),
    );

    await tester.pumpWidget(_localized(const ExceptionsScreen()));
    await _pumpUntil(tester, find.text('No exceptions'));

    expect(find.widgetWithText(FilterChip, 'All'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Open'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Escalated'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Resolved'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Dismissed'), findsOneWidget);
  });

  testWidgets('tapping a status chip narrows the request', (tester) async {
    String? lastStatus;
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        lastStatus = req.url.queryParameters['status'];
        return _list([]);
      }),
    );

    await tester.pumpWidget(_localized(const ExceptionsScreen()));
    await _pumpUntil(tester, find.text('No exceptions'));

    await tester.tap(find.widgetWithText(FilterChip, 'Escalated'));
    await tester.pump(const Duration(milliseconds: 50));

    expect(store.statusFilter, 'escalated');
    expect(lastStatus, 'escalated');
  });

  testWidgets('the action sheet resolve calls the store and POSTs resolve',
      (tester) async {
    String? sentAction;
    var listCalls = 0;
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.method == 'POST' && req.url.path.endsWith('/resolve')) {
          sentAction =
              (jsonDecode(req.body) as Map<String, dynamic>)['action']
                  as String?;
          return http.Response('{}', 200);
        }
        listCalls++;
        return _list([_exceptionJson('1')]);
      }),
    );

    await tester.pumpWidget(_localized(const ExceptionsScreen()));
    await _pumpUntil(tester, find.byType(ExceptionListTile));

    // Tapping a row opens the action sheet; let it animate fully in so the
    // sheet items are at their final, hit-testable positions.
    await tester.tap(find.byType(ExceptionListTile).first);
    await tester.pumpAndSettle();
    expect(find.text('Escalate'), findsOneWidget);

    // initial load = 1 list call; resolve POSTs then refetches → wait for the
    // refetch (the real signal) rather than a fixed delay.
    await tester.tap(find.text('Resolve'));
    await _pumpUntilTrue(tester, () => sentAction != null && listCalls >= 2);

    expect(sentAction, 'resolve');
    expect(listCalls, greaterThanOrEqualTo(2),
        reason: 'resolve should trigger a refetch after the initial load');
  });

  testWidgets('swiping a row right resolves the exception', (tester) async {
    String? sentAction;
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.method == 'POST' && req.url.path.endsWith('/resolve')) {
          sentAction =
              (jsonDecode(req.body) as Map<String, dynamic>)['action']
                  as String?;
          return http.Response('{}', 200);
        }
        return _list([_exceptionJson('1')]);
      }),
    );

    await tester.pumpWidget(_localized(const ExceptionsScreen()));
    await _pumpUntil(tester, find.byType(ExceptionListTile));

    await tester.drag(
      find.byType(ExceptionListTile).first,
      const Offset(600, 0),
    );
    // Pump frames to drive the dismiss animation + confirmDismiss callback, then
    // wait on the real signal (the POST landing), not a fixed delay.
    await _pumpUntilTrue(tester, () => sentAction != null);

    expect(sentAction, 'resolve');
  });
}
