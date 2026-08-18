// Regression coverage for a setState-after-dispose on the contract detail
// screen — the same shape as `invoice_detail_screen`'s `_load()`: await the
// GET, then setState in BOTH branches with no `mounted` check. Backing out of
// a contract before a slow GET (or its 10s timeout) resolved threw
// "setState() called after dispose()"; the FlutterError that raised was then
// caught by the network-error `catch`, whose own unguarded setState threw
// again — this time out of the async gap entirely.
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:feohledger_mobile/api/api_client.dart';
import 'package:feohledger_mobile/l10n/gen/app_localizations.dart';
import 'package:feohledger_mobile/screens/contract_detail_screen.dart';
import 'package:feohledger_mobile/services/offline_store.dart';

http.Response _json(Object body, [int status = 200]) => http.Response(
      jsonEncode(body),
      status,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _contractJson() => {
      'id': 'c1',
      'contract_number': 'CTR-001',
      'title': 'Cleaning services',
      'contract_type': 'service',
      'status': 'active',
      'vendor_name': 'Acme Corp',
      'currency': 'USD',
      'created_at': '2026-01-01T12:00:00',
    };

void main() {
  setUpAll(() async {
    OfflineStore.instance.debugUseMemory();
  });

  setUp(() async {
    FlutterSecureStorage.setMockInitialValues({});
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
  });

  /// Push the detail route, pop it while its GET is still in flight, then
  /// release the response. Waits on the real signal (the route leaving the
  /// tree), never a fixed delay.
  Future<void> pushPopComplete(
    WidgetTester tester,
    Completer<void> gate,
  ) async {
    final navigator = GlobalKey<NavigatorState>();
    await tester.pumpWidget(MaterialApp(
      navigatorKey: navigator,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: const Scaffold(body: Text('list')),
    ));

    navigator.currentState!.push(
      MaterialPageRoute(
        builder: (_) => const ContractDetailScreen(contractId: 'c1'),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.byType(ContractDetailScreen), findsOneWidget);

    navigator.currentState!.pop();
    await tester.pump();
    final gone = find.byType(ContractDetailScreen);
    for (var i = 0; i < 40 && gone.evaluate().isNotEmpty; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(gone, findsNothing);

    gate.complete();
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
  }

  testWidgets('a late success response does not setState after dispose',
      (tester) async {
    final gate = Completer<void>();
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        await gate.future;
        return _json(_contractJson());
      }),
    );

    await pushPopComplete(tester, gate);

    expect(tester.takeException(), isNull);
  });

  testWidgets('a late failure response does not setState after dispose',
      (tester) async {
    final gate = Completer<void>();
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        await gate.future;
        return http.Response('boom', 500);
      }),
    );

    await pushPopComplete(tester, gate);

    expect(tester.takeException(), isNull);
  });
}
