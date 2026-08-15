// Regression coverage for issue #183: getList, patch and delete had no
// network timeout guard, so a connected-but-silent backend hung the awaited
// http call forever — the store's catch block (which drives the offline-cache
// fallback) never ran. get/post already had a `.timeout(...)` guard; this
// file proves getList/patch/delete now match that pattern.
//
// The 10s production timeout is shrunk via `ApiClient().debugConfigure(
// timeout: ...)` (a test-only seam — see api_client.dart) so these tests
// don't burn 10 real seconds per case.

// The `error logging` group at the bottom covers a second defect in the same
// file: get/post returned `_handleResponse(response)` unawaited from inside
// their try block, so the catch never ran for a 4xx/5xx.
//
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:feohledger_mobile/api/api_client.dart';

void main() {
  setUp(() {
    // Reset to a clean session before every test; timeout defaults back to
    // the real 10s unless a test overrides it below.
    ApiClient().debugConfigure();
  });

  group('getList', () {
    test('times out instead of hanging forever on a silent network',
        () async {
      ApiClient().debugConfigure(
        client: MockClient((req) => Completer<http.Response>().future),
        timeout: const Duration(milliseconds: 50),
      );

      await expectLater(
        ApiClient().getList('/things'),
        throwsA(isA<TimeoutException>()),
      );
    });

    test('a normal fast call still succeeds', () async {
      ApiClient().debugConfigure(
        client: MockClient(
          (req) async => http.Response(
            jsonEncode({
              'items': [
                {'id': '1'},
                {'id': '2'},
              ],
            }),
            200,
            headers: {'content-type': 'application/json'},
          ),
        ),
        timeout: const Duration(milliseconds: 50),
      );

      final result = await ApiClient().getList('/things');

      expect(result, hasLength(2));
      expect(result.first['id'], '1');
    });
  });

  group('patch', () {
    test('times out instead of hanging forever on a silent network',
        () async {
      ApiClient().debugConfigure(
        client: MockClient((req) => Completer<http.Response>().future),
        timeout: const Duration(milliseconds: 50),
      );

      await expectLater(
        ApiClient().patch('/things/1', {'name': 'updated'}),
        throwsA(isA<TimeoutException>()),
      );
    });

    test('a normal fast call still succeeds', () async {
      ApiClient().debugConfigure(
        client: MockClient(
          (req) async => http.Response(
            jsonEncode({'id': '1', 'name': 'updated'}),
            200,
            headers: {'content-type': 'application/json'},
          ),
        ),
        timeout: const Duration(milliseconds: 50),
      );

      final result = await ApiClient().patch('/things/1', {'name': 'updated'});

      expect(result['name'], 'updated');
    });
  });

  group('delete', () {
    test('times out instead of hanging forever on a silent network',
        () async {
      ApiClient().debugConfigure(
        client: MockClient((req) => Completer<http.Response>().future),
        timeout: const Duration(milliseconds: 50),
      );

      await expectLater(
        ApiClient().delete('/things/1'),
        throwsA(isA<TimeoutException>()),
      );
    });

    test('a normal fast call still succeeds', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('', 204)),
        timeout: const Duration(milliseconds: 50),
      );

      await expectLater(ApiClient().delete('/things/1'), completes);
    });
  });

  // `get` and `post` wrap their call in try/catch purely to log the failure
  // before rethrowing — but they returned `_handleResponse(response)` without
  // awaiting it, which unwinds the try and hands the still-pending future to
  // the caller. Every error _handleResponse raises (ApiException on 4xx/5xx,
  // a decode failure on a malformed body) therefore bypassed the catch, and
  // the diagnostic never fired for the exact responses it exists for. The
  // exception still reached the caller, so only the log was lost — which is
  // why nothing else caught it. Dart's `unawaited_return_in_try_block` lint
  // (new in the analyzer CI runs) flags the shape; these pin the behaviour.
  group('error logging', () {
    final logged = <String>[];
    late DebugPrintCallback originalDebugPrint;

    setUp(() {
      logged.clear();
      originalDebugPrint = debugPrint;
      debugPrint = (String? message, {int? wrapWidth}) {
        if (message != null) logged.add(message);
      };
    });

    tearDown(() {
      debugPrint = originalDebugPrint;
    });

    test('get logs the failure when the backend returns 500', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
        timeout: const Duration(milliseconds: 50),
      );

      await expectLater(
        ApiClient().get('/things'),
        throwsA(isA<ApiException>()),
      );
      expect(
        logged.any((m) => m.startsWith('[API] GET /things FAILED:')),
        isTrue,
        reason: 'the catch block never ran for the error response',
      );
    });

    test('post logs the failure when the backend returns 500', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
        timeout: const Duration(milliseconds: 50),
      );

      await expectLater(
        ApiClient().post('/things', {'name': 'x'}),
        throwsA(isA<ApiException>()),
      );
      expect(
        logged.any((m) => m.startsWith('[API] POST /things FAILED:')),
        isTrue,
        reason: 'the catch block never ran for the error response',
      );
    });
  });
}
