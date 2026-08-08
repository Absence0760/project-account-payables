// Coverage for NotificationApi.registerDeviceToken — the mobile half of
// closing out the push-service device-token TODO. Proves the request hits
// the right path with the right body shape; the backend contract itself is
// covered by backend/tests/test_notifications.py.

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:feohledger_mobile/api/api_client.dart';
import 'package:feohledger_mobile/api/endpoints.dart';

void main() {
  setUp(() {
    ApiClient().debugConfigure();
  });

  group('NotificationApi.registerDeviceToken', () {
    test('POSTs the token + platform to /notifications/device-token',
        () async {
      http.Request? captured;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          captured = req;
          return http.Response(
            jsonEncode({'platform': 'ios', 'updated_at': '2026-01-01T00:00:00Z'}),
            200,
            headers: {'content-type': 'application/json'},
          );
        }),
      );

      await NotificationApi.registerDeviceToken('fcm-token-abc', 'ios');

      expect(captured, isNotNull);
      expect(captured!.method, 'POST');
      expect(captured!.url.path, endsWith('/notifications/device-token'));
      final body = jsonDecode(captured!.body) as Map<String, dynamic>;
      expect(body['token'], 'fcm-token-abc');
      expect(body['platform'], 'ios');
    });

    test('propagates a backend error instead of swallowing it', () async {
      ApiClient().debugConfigure(
        client: MockClient(
          (req) async => http.Response('{"detail":"bad"}', 422),
        ),
      );

      await expectLater(
        NotificationApi.registerDeviceToken('fcm-token-abc', 'android'),
        throwsA(isA<ApiException>()),
      );
    });
  });
}
