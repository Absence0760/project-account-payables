import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/stores/auth_store.dart';

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

/// A MockClient that logs a user in with the given [roles] and 200s on logout.
MockClient _happyClient(List<String> roles) {
  return MockClient((req) async {
    final path = req.url.path;
    if (req.method == 'POST' && path == '/api/auth/login') {
      return _json({'access_token': 'tok-123'});
    }
    if (req.method == 'GET' && path == '/api/auth/me') {
      return _json(_meBody(roles));
    }
    if (req.method == 'POST' && path == '/api/auth/logout') {
      return _json({});
    }
    return http.Response('not found', 404);
  });
}

void main() {
  final store = AuthStore.instance;

  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
  });

  Future<void> loginAs(List<String> roles) async {
    ApiClient().debugConfigure(client: _happyClient(roles));
    final ok = await store.login('demo@acme.com', 'demo', 'acme');
    expect(ok, isTrue);
  }

  group('login', () {
    test('success populates the user and clears loading/error', () async {
      await loginAs(['admin']);

      expect(store.loggedIn, isTrue);
      expect(store.user?.email, 'demo@acme.com');
      expect(store.loading, isFalse);
      expect(store.error, isNull);
    });

    test('persists the token on the ApiClient', () async {
      await loginAs(['ap_clerk']);
      expect(ApiClient().hasToken, isTrue);
    });

    test('invalid credentials (4xx) surface a friendly error', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('bad creds', 401)),
      );

      final ok = await store.login('demo@acme.com', 'wrong', 'acme');

      expect(ok, isFalse);
      expect(store.error, 'Invalid credentials');
      expect(store.loading, isFalse);
    });

    test('a transport failure surfaces a connection error', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => throw Exception('network down')),
      );

      final ok = await store.login('demo@acme.com', 'demo', 'acme');

      expect(ok, isFalse);
      expect(store.error, startsWith('Connection failed'));
    });

    test('notifies listeners on the loading->done transition', () async {
      ApiClient().debugConfigure(client: _happyClient(['admin']));
      var notifications = 0;
      void listener() => notifications++;
      store.addListener(listener);

      await store.login('demo@acme.com', 'demo', 'acme');
      store.removeListener(listener);

      // at least: loading=true notify, then success notify
      expect(notifications, greaterThanOrEqualTo(2));
    });
  });

  group('role gating', () {
    test('admin can approve and view payments', () async {
      await loginAs(['admin']);
      expect(store.canApprove, isTrue);
      expect(store.canViewPayments, isTrue);
    });

    test('ap_manager can approve and view payments', () async {
      await loginAs(['ap_manager']);
      expect(store.canApprove, isTrue);
      expect(store.canViewPayments, isTrue);
    });

    test('cfo can view payments but cannot approve', () async {
      await loginAs(['cfo']);
      expect(store.canApprove, isFalse);
      expect(store.canViewPayments, isTrue);
    });

    test('ap_clerk can neither approve nor view payments', () async {
      await loginAs(['ap_clerk']);
      expect(store.canApprove, isFalse);
      expect(store.canViewPayments, isFalse);
      expect(store.isClerkOnly, isTrue);
    });
  });

  group('logout', () {
    test('clears the user', () async {
      await loginAs(['admin']);
      expect(store.loggedIn, isTrue);

      await store.logout();

      expect(store.loggedIn, isFalse);
      expect(store.user, isNull);
    });
  });
}
