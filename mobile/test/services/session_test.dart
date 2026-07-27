import 'dart:convert';
import 'dart:io';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:feohledger_mobile/api/api_client.dart';
import 'package:feohledger_mobile/services/offline_store.dart';
import 'package:feohledger_mobile/services/session.dart';
import 'package:feohledger_mobile/stores/auth_store.dart';
import 'package:feohledger_mobile/stores/dashboard_store.dart';
import 'package:feohledger_mobile/stores/invoice_store.dart';

/// Session-scoped local state (issue #176): the offline cache and the store
/// singletons outlive a session on a device, so they must be namespaced by
/// `(tenant, user)` AND torn down on every sign-out — otherwise a device
/// reused by another user serves the previous tenant's financial data from
/// cache whenever a live fetch fails.

http.Response _json(Object body, [int status = 200]) => http.Response(
      jsonEncode(body),
      status,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _dashboardBody(int totalInvoices) => {
      'total_invoices': totalInvoices,
      'total_amount': 1000.0,
      'pipeline': {'pending': 2},
      'vendor_spend': [
        {'vendor': 'Acme', 'amount': 600, 'invoice_count': 3},
      ],
      'aging': {'current': 100, 'days_30': 200},
      'monthly_trend': [
        {'month': '2026-01', 'count': 5, 'amount': 1000},
      ],
      'upcoming_payments': [],
    };

/// Signs a user in and serves a dashboard with [totalInvoices] as the tell.
/// When [dashboardOffline] the dashboard endpoint throws instead, which is the
/// condition that makes the app fall back to the offline cache.
MockClient _client({
  required String userId,
  int totalInvoices = 1,
  bool dashboardOffline = false,
}) {
  return MockClient((req) async {
    final path = req.url.path;
    if (req.method == 'POST' && path == '/api/auth/login') {
      return _json({'access_token': 'tok-$userId'});
    }
    if (req.method == 'POST' && path == '/api/auth/logout') {
      return _json({});
    }
    if (req.method == 'GET' && path == '/api/auth/me') {
      return _json({
        'id': userId,
        'email': '$userId@example.com',
        'full_name': 'User $userId',
        'organization_id': 'org-$userId',
        'roles': ['admin'],
      });
    }
    if (req.method == 'GET' && path == '/api/dashboard') {
      if (dashboardOffline) throw Exception('offline');
      return _json(_dashboardBody(totalInvoices));
    }
    return http.Response('not found', 404);
  });
}

Future<void> _login({
  required String tenant,
  required String userId,
  int totalInvoices = 1,
  bool dashboardOffline = false,
}) async {
  ApiClient().debugConfigure(
    client: _client(
      userId: userId,
      totalInvoices: totalInvoices,
      dashboardOffline: dashboardOffline,
    ),
  );
  final result = await AuthStore.instance.login('u@example.com', 'pw', tenant);
  expect(result.isSuccess, isTrue);
}

void main() {
  setUp(() async {
    FlutterSecureStorage.setMockInitialValues({});
    // Private in-memory cache so parallel test isolates don't share a file.
    OfflineStore.instance.debugUseMemory();
    SessionManager.resetStores();
  });

  group('cross-session isolation', () {
    test(
        'user B never sees user A cached dashboard after a device hand-over',
        () async {
      // User A (tenant acme) works online — the dashboard lands in the cache.
      await _login(tenant: 'acme', userId: 'user-a', totalInvoices: 42);
      await DashboardStore.instance.fetch();
      expect(DashboardStore.instance.data!.totalInvoices, 42);
      expect(DashboardStore.instance.fromCache, isFalse);

      await AuthStore.instance.logout();

      // User B (tenant globex) signs in on the same device and goes offline.
      await _login(tenant: 'globex', userId: 'user-b', dashboardOffline: true);
      await DashboardStore.instance.fetch();

      expect(
        DashboardStore.instance.data,
        isNull,
        reason: "B must never be shown A's cached dashboard",
      );
      expect(DashboardStore.instance.fromCache, isFalse);
      expect(DashboardStore.instance.error, isNotNull);
    });

    test('the same user in a different tenant cannot read the other cache',
        () async {
      await _login(tenant: 'acme', userId: 'user-a', totalInvoices: 7);
      await DashboardStore.instance.fetch();
      await AuthStore.instance.logout();

      // Same person, second tenant they belong to, no connectivity.
      await _login(tenant: 'globex', userId: 'user-a', dashboardOffline: true);
      await DashboardStore.instance.fetch();

      expect(DashboardStore.instance.data, isNull);
      expect(DashboardStore.instance.error, isNotNull);
    });
  });

  group('logout teardown', () {
    test('logout clears the offline cache — even for the same user', () async {
      await _login(tenant: 'acme', userId: 'user-a', totalInvoices: 5);
      await DashboardStore.instance.fetch();
      expect(DashboardStore.instance.data!.totalInvoices, 5);

      await AuthStore.instance.logout();

      // Same tenant + user back in, but offline: nothing left to serve.
      await _login(tenant: 'acme', userId: 'user-a', dashboardOffline: true);
      await DashboardStore.instance.fetch();

      expect(DashboardStore.instance.data, isNull);
      expect(DashboardStore.instance.error, isNotNull);
    });

    test('logout resets the store singletons and the auth state', () async {
      await _login(tenant: 'acme', userId: 'user-a', totalInvoices: 5);
      await DashboardStore.instance.fetch();
      expect(DashboardStore.instance.data, isNotNull);
      expect(AuthStore.instance.loggedIn, isTrue);

      await AuthStore.instance.logout();

      expect(DashboardStore.instance.data, isNull);
      expect(DashboardStore.instance.fromCache, isFalse);
      expect(InvoiceStore.instance.invoices, isEmpty);
      expect(AuthStore.instance.loggedIn, isFalse);
      expect(AuthStore.instance.user, isNull);
    });

    test('a 401 (expired / revoked token) runs the same teardown', () async {
      await _login(tenant: 'acme', userId: 'user-a', totalInvoices: 5);
      await DashboardStore.instance.fetch();
      expect(DashboardStore.instance.data, isNotNull);

      // Token revoked server-side: every call now 401s.
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('nope', 401)),
      );
      // The ApiClient keeps the restored token; re-arm it so the 401 path is
      // what ends the session rather than a missing credential.
      await ApiClient().setToken('tok-user-a');
      await DashboardStore.instance.fetch();

      expect(AuthStore.instance.loggedIn, isFalse);
      expect(OfflineStore.instance.hasScope, isFalse);
      expect(await OfflineStore.instance.get('dashboard'), isNull);
    });
  });

  group('OfflineStore scoping', () {
    test('keys are namespaced per (tenant, user)', () async {
      await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');
      await OfflineStore.instance.put('invoices_all_', [
        {'id': 'inv-1'},
      ]);
      expect(await OfflineStore.instance.get('invoices_all_'), isNotNull);

      await OfflineStore.instance.setScope(tenantSlug: 'globex', userId: 'u1');
      expect(await OfflineStore.instance.get('invoices_all_'), isNull);

      await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u2');
      expect(await OfflineStore.instance.get('invoices_all_'), isNull);
    });

    test('a scope change purges the rows of the previous scope', () async {
      await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');
      await OfflineStore.instance.put('dashboard', {'total_invoices': 3});

      // Someone else uses the device...
      await OfflineStore.instance.setScope(tenantSlug: 'globex', userId: 'u2');
      // ...and the first user comes back: their rows are gone, not just hidden.
      await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');

      expect(await OfflineStore.instance.get('dashboard'), isNull);
    });

    test('re-installing the same scope keeps that session cache', () async {
      await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');
      await OfflineStore.instance.put('dashboard', {'total_invoices': 3});

      final changed = await OfflineStore.instance.setScope(
        tenantSlug: 'acme',
        userId: 'u1',
      );

      expect(changed, isFalse);
      expect(await OfflineStore.instance.get('dashboard'), isNotNull);
    });

    test('scope components cannot be forged by a crafted slug', () async {
      await OfflineStore.instance.setScope(tenantSlug: 'a|b', userId: 'c');
      await OfflineStore.instance.put('dashboard', {'total_invoices': 1});

      await OfflineStore.instance.setScope(tenantSlug: 'a', userId: 'b|c');

      expect(await OfflineStore.instance.get('dashboard'), isNull);
    });

    test('with no scope the cache is inert (fails closed)', () async {
      await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');
      await OfflineStore.instance.put('dashboard', {'total_invoices': 3});

      OfflineStore.instance.clearScope();

      expect(OfflineStore.instance.hasScope, isFalse);
      expect(await OfflineStore.instance.get('dashboard'), isNull);

      // Writes are dropped rather than landing unattributed — signing back in
      // finds the pre-existing row untouched by the unscoped write.
      await OfflineStore.instance.put('dashboard', {'total_invoices': 9});
      await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');
      final cached = await OfflineStore.instance.get('dashboard');
      expect((cached as Map)['total_invoices'], 3);
    });
  });

  test('every account-scoped store is reset by SessionManager.resetStores',
      () {
    // A store singleton that nobody resets keeps one account's data in memory
    // for the next one — so adding a store must mean wiring it in here.
    // Asserts the actual `X.instance.reset();` CALL, not merely an import: a
    // store imported for some other reason must not satisfy this guard.
    //
    // Exempt: files under lib/stores/ that are NOT account-scoped store
    // singletons — `locale_store.dart` is a device preference (display
    // language), and `sequenced_fetch.dart` is the `SequencedFetch` mixin (a
    // per-store request-sequence helper: no `.instance`, no account data to
    // clear — the store it's mixed into is what resets).
    const exempt = {'locale_store.dart', 'sequenced_fetch.dart'};
    final session = File('lib/services/session.dart').readAsStringSync();
    final body = session.substring(session.indexOf('static void resetStores()'));

    String className(String fileName) => fileName
        .replaceAll('.dart', '')
        .split('_')
        .map((part) => part[0].toUpperCase() + part.substring(1))
        .join();

    final missing = Directory('lib/stores')
        .listSync()
        .whereType<File>()
        .map((f) => f.uri.pathSegments.last)
        .where((name) => name.endsWith('.dart') && !exempt.contains(name))
        .where((name) => !body.contains('${className(name)}.instance.reset();'))
        .toList();

    expect(missing, isEmpty, reason: 'not reset on logout: $missing');
  });
}
