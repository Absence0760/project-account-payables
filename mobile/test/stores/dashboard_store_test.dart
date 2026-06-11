import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/dashboard_store.dart';

http.Response _dashboard({int totalInvoices = 5, double totalAmount = 1000}) =>
    http.Response(
      jsonEncode({
        'total_invoices': totalInvoices,
        'total_amount': totalAmount,
        'pipeline': {'pending': 2},
        'vendor_spend': [
          {'vendor': 'Acme', 'amount': 600, 'invoice_count': 3},
        ],
        'aging': {'current': 100, 'days_30': 200},
        'monthly_trend': [
          {'month': '2026-01', 'count': 5, 'amount': 1000},
        ],
        'upcoming_payments': [],
      }),
      200,
      headers: {'content-type': 'application/json'},
    );

void main() {
  final store = DashboardStore.instance;

  setUpAll(() async {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
    // Private in-memory cache so parallel test isolates don't share a file.
    await OfflineStore.instance.debugUseInMemory();
  });

  setUp(() async {
    await OfflineStore.instance.clear();
    ApiClient().debugConfigure();
  });

  test('fetch success populates data from the network', () async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _dashboard(totalInvoices: 7)),
    );

    await store.fetch();

    expect(store.data, isNotNull);
    expect(store.data!.totalInvoices, 7);
    expect(store.data!.topVendors.first.vendorName, 'Acme');
    expect(store.fromCache, isFalse);
    expect(store.error, isNull);
  });

  test('falls back to cached dashboard data when the network fails', () async {
    // prime the cache
    ApiClient().debugConfigure(
      client: MockClient((req) async => _dashboard(totalInvoices: 9)),
    );
    await store.fetch();
    expect(store.fromCache, isFalse);

    // network fails -> serve cached copy
    ApiClient().debugConfigure(
      client: MockClient((req) async => throw Exception('offline')),
    );
    await store.fetch();

    expect(store.data, isNotNull);
    expect(store.data!.totalInvoices, 9);
    expect(store.fromCache, isTrue);
  });

  test('surfaces an error when network fails and no cache exists', () async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => throw Exception('offline')),
    );

    await store.fetch();

    expect(store.error, isNotNull);
    expect(store.fromCache, isFalse);
  });
}
