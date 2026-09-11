import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:feohledger_mobile/api/api_client.dart';
import 'package:feohledger_mobile/models/adaptive.dart';
import 'package:feohledger_mobile/stores/adaptive_store.dart';

http.Response _json(Object body, [int status = 200]) => http.Response(
  jsonEncode(body),
  status,
  headers: {'content-type': 'application/json'},
);

Map<String, dynamic> _suggestionJson(
  String id, {
  String status = 'open',
  String title = 'Vendor Acme: 14/14 invoices approved unmodified',
}) => {
  'id': id,
  'kind': 'auto_approve_threshold',
  'vendor_id': 'v1',
  'vendor_name': 'Acme',
  'title': title,
  'rationale': '14 invoices approved with no corrections and 0 rejections.',
  'payload': {'suggested_threshold': '2000.00'},
  'confidence_pct': '95.00',
  'status': status,
  'created_at': '2026-03-01T10:00:00',
  'dismissed_at': null,
};

Map<String, dynamic> _patternsJson() => {
  'generated_at': '2026-03-04T10:00:00',
  'lookback_days': 180,
  'entity_id': null,
  'approvers': [
    {
      'approver_id': 'u1',
      'approver_name': 'Ada Lovelace',
      'approved_count': 12,
      'rejected_count': 1,
      'approval_rate_pct': '92.31',
      'median_time_to_approve_days': '1.50',
      'avg_time_to_approve_days': '2.10',
      'sample_size': 13,
    },
  ],
  'vendors': [
    {
      'vendor_id': 'v1',
      'vendor_name': 'Acme',
      'approved_count': 14,
      'rejected_count': 0,
      'approval_rate_pct': '100.00',
      'unmodified_count': 14,
      'consistency_pct': '100.00',
      'avg_approved_amount': '1200.00',
      'median_approved_amount': '1150.00',
      'min_approved_amount': '400.00',
      'max_approved_amount': '1900.00',
      'sample_size': 14,
      'unconverted_count': 2,
    },
  ],
};

Map<String, dynamic> _anomaliesJson() => {
  'total_scanned': 7,
  'flagged': [
    {
      'invoice_id': 'inv1',
      'vendor_id': 'v1',
      'vendor_name': 'Acme',
      'amount': '9000.00',
      'amount_currency': 'USD',
      'insufficient_history': false,
      'baseline': {
        'vendor_id': 'v1',
        'vendor_name': 'Acme',
        'sample_size': 14,
        'mean_amount': '1200.00',
        'median_amount': '1150.00',
        'stdev_amount': '300.00',
        'min_amount': '400.00',
        'max_amount': '1900.00',
        'typical_approver_ids': ['u1'],
        'median_time_to_approve_days': '1.50',
      },
      'flags': [
        {
          'code': 'amount_outlier',
          'severity': 'warning',
          'message': 'Amount is 26 standard deviations above this vendor’s mean.',
          'observed': '9000.00',
          'expected': '1200.00',
        },
      ],
    },
  ],
};

void main() {
  final store = AdaptiveStore.instance;

  setUp(() {
    AdaptiveStore.instance.reset();
    ApiClient().debugConfigure();
  });

  group('suggestions', () {
    test('fetch populates the list and parses each row', () async {
      ApiClient().debugConfigure(
        client: MockClient(
          (req) async => _json({'suggestions': [_suggestionJson('s1')]}),
        ),
      );

      await store.fetchSuggestions();

      expect(store.suggestions, hasLength(1));
      expect(store.suggestionsLoading, isFalse);
      expect(store.suggestionsError, isNull);
      final s = store.suggestions.first;
      expect(s.id, 's1');
      expect(s.status, SuggestionStatus.open);
      expect(s.isOpen, isTrue);
      // Statistics stay strings — the backend stringifies its Decimals.
      expect(s.confidencePct, '95.00');
    });

    test('an unrecognised status is unknown, never silently open', () async {
      ApiClient().debugConfigure(
        client: MockClient(
          (req) async => _json({
            'suggestions': [_suggestionJson('s1', status: 'superseded')],
          }),
        ),
      );

      await store.fetchSuggestions();

      expect(store.suggestions.first.status, SuggestionStatus.unknown);
      expect(store.suggestions.first.isOpen, isFalse);
    });

    test('fetch surfaces an error and leaves the list empty', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      await store.fetchSuggestions();

      expect(store.suggestionsError, isNotNull);
      expect(store.suggestions, isEmpty);
      expect(store.suggestionsLoading, isFalse);
    });

    test('the status filter is a server-side query param', () async {
      final statuses = <String?>[];
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          statuses.add(req.url.queryParameters['status']);
          return _json({'suggestions': [_suggestionJson('s1')]});
        }),
      );

      await store.fetchSuggestions();
      await store.setSuggestionStatus('all');

      expect(store.suggestionStatus, 'all');
      expect(statuses, ['open', 'all']);
    });

    test('re-selecting the current status does not re-hit the API', () async {
      var calls = 0;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          calls++;
          return _json({'suggestions': const []});
        }),
      );

      await store.fetchSuggestions();
      await store.setSuggestionStatus('open');

      expect(calls, 1);
    });

    test('dismiss under the open filter takes the row out of the list',
        () async {
      String? posted;
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST') {
            posted = req.url.path;
            return _json({
              'suggestions': [_suggestionJson('s1', status: 'dismissed')],
            });
          }
          return _json({
            'suggestions': [_suggestionJson('s1'), _suggestionJson('s2')],
          });
        }),
      );
      await store.fetchSuggestions();
      expect(store.suggestions, hasLength(2));

      final ok = await store.dismissSuggestion('s1');

      expect(ok, isTrue);
      expect(posted, '/api/adaptive/suggestions/s1/dismiss');
      // Patched in place rather than refetched: `GET /suggestions` recomputes
      // and upserts the whole derived set on every read.
      expect(store.suggestions.map((s) => s.id), ['s2']);
    });

    test('dismiss under the all filter flips the row in place', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST') {
            return _json({
              'suggestions': [_suggestionJson('s1', status: 'dismissed')],
            });
          }
          return _json({
            'suggestions': [_suggestionJson('s1'), _suggestionJson('s2')],
          });
        }),
      );
      await store.setSuggestionStatus('all');
      expect(store.suggestions, hasLength(2));

      await store.dismissSuggestion('s1');

      expect(store.suggestions, hasLength(2));
      expect(store.suggestions.first.status, SuggestionStatus.dismissed);
    });

    test('a refused dismiss returns false, records the error, keeps the row',
        () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.method == 'POST') {
            return _json({'detail': 'Your role does not permit this.'}, 403);
          }
          return _json({'suggestions': [_suggestionJson('s1')]});
        }),
      );
      await store.fetchSuggestions();

      final ok = await store.dismissSuggestion('s1');

      expect(ok, isFalse);
      expect(store.suggestionsError, contains('Your role does not permit this.'));
      expect(store.suggestions, hasLength(1));
    });
  });

  group('approval patterns', () {
    test('fetch parses approvers and vendors', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => _json(_patternsJson())),
      );

      await store.fetchPatterns();

      final data = store.patterns!;
      expect(data.lookbackDays, 180);
      expect(data.approvers.single.approverName, 'Ada Lovelace');
      expect(data.approvers.single.approvalRatePct, '92.31');
      expect(data.vendors.single.vendorName, 'Acme');
      expect(data.vendors.single.medianApprovedAmount, '1150.00');
      // Disclosed, not dropped: excluded approvals are still in the sample.
      expect(data.vendors.single.unconvertedCount, 2);
      expect(store.patternsError, isNull);
    });

    test('fetch surfaces an error', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      await store.fetchPatterns();

      expect(store.patternsError, isNotNull);
      expect(store.patterns, isNull);
    });
  });

  group('anomalies', () {
    test('fetch parses the scanned count and the flags', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => _json(_anomaliesJson())),
      );

      await store.fetchAnomalies();

      final batch = store.anomalies!;
      // The scanned count is what makes an empty `flagged` mean "nothing stands
      // out" rather than "nothing was looked at".
      expect(batch.totalScanned, 7);
      expect(batch.flagged.single.amount, '9000.00');
      expect(batch.flagged.single.amountCurrency, 'USD');
      expect(batch.flagged.single.flags.single.severity, 'warning');
    });

    test('fetch surfaces an error', () async {
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response('boom', 500)),
      );

      await store.fetchAnomalies();

      expect(store.anomaliesError, isNotNull);
      expect(store.anomalies, isNull);
    });
  });

  test('a failing section does not disturb a loaded sibling', () async {
    // The reason each section owns its own state: a reader looking at the
    // patterns tab must not have it blanked by the anomaly scan failing.
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.url.path.endsWith('/approval-patterns')) {
          return _json(_patternsJson());
        }
        return http.Response('boom', 500);
      }),
    );

    await store.fetchPatterns();
    await store.fetchAnomalies();

    expect(store.patterns, isNotNull);
    expect(store.patternsError, isNull);
    expect(store.anomalies, isNull);
    expect(store.anomaliesError, isNotNull);
  });

  test('reset clears every section and the status filter', () async {
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.url.path.endsWith('/approval-patterns')) {
          return _json(_patternsJson());
        }
        return _json({'suggestions': [_suggestionJson('s1')]});
      }),
    );
    await store.fetchPatterns();
    await store.setSuggestionStatus('all');

    store.reset();

    expect(store.patterns, isNull);
    expect(store.anomalies, isNull);
    expect(store.suggestions, isEmpty);
    expect(store.suggestionStatus, 'open');
    expect(store.patternsError, isNull);
  });
}
