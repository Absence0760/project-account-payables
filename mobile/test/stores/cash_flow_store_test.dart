import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:feohledger_mobile/api/api_client.dart';
import 'package:feohledger_mobile/stores/cash_flow_store.dart';

Map<String, dynamic> _forecastBody({
  int horizonDays = 90,
  List<Map<String, dynamic>>? periods,
  Map<String, dynamic>? totals,
}) =>
    {
      'granularity': 'week',
      'horizon_days': horizonDays,
      'include_pending': true,
      'generated_at': '2026-06-20T00:00:00Z',
      'periods': periods ??
          [
            {
              'period': '2026-W26',
              'scheduled_amount': 3000.0,
              'committed_amount': 2000.0,
              'pending_amount': 1000.0,
              'discount_eligible_amount': 500.0,
              'count': 4,
            },
          ],
      'totals': totals ??
          {
            'scheduled_amount': 3000.0,
            'committed_amount': 2000.0,
            'pending_amount': 1000.0,
            'discount_eligible_amount': 500.0,
            'count': 4,
          },
    };

Map<String, dynamic> _positionBody({
  double opening = 10000.0,
  double? threshold = 5000.0,
  List<Map<String, dynamic>>? periods,
  List<Map<String, dynamic>>? breaches,
}) =>
    {
      'granularity': 'week',
      'horizon_days': 90,
      'opening_balance': opening,
      'opening_balance_source': 'settings',
      'opening_balance_currency': null,
      'threshold': threshold,
      'periods': periods ??
          [
            {
              'period': '2026-W26',
              'period_start': '2026-06-22',
              'period_end': '2026-06-28',
              'opening': 10000.0,
              'outflow': 3000.0,
              'inflow': 0.0,
              'closing': 7000.0,
              'below_threshold': false,
            },
          ],
      'breaches': breaches ?? [],
    };

http.Response _json(Object body, [int status = 200]) => http.Response(
      jsonEncode(body),
      status,
      headers: {'content-type': 'application/json'},
    );

/// Route the two endpoints the store hits to the right fixture.
MockClient _client({
  Map<String, dynamic>? forecast,
  Map<String, dynamic>? position,
}) =>
    MockClient((req) async {
      if (req.url.path.endsWith('/analytics/cashflow_forecast')) {
        return _json(forecast ?? _forecastBody());
      }
      if (req.url.path.endsWith('/analytics/cash_position')) {
        return _json(position ?? _positionBody());
      }
      return http.Response('not found', 404);
    });

void main() {
  final store = CashFlowStore.instance;

  setUp(() {
    store.reset();
    ApiClient().debugConfigure();
  });

  test('fetch combines forecast + cash position into one payload', () async {
    ApiClient().debugConfigure(client: _client());

    await store.fetch();

    expect(store.error, isNull);
    expect(store.loading, isFalse);
    final data = store.data!;
    expect(data.horizonDays, 90);
    expect(data.forecastPeriods, hasLength(1));
    expect(data.forecastPeriods.first.period, '2026-W26');
    // Money carried through as display strings, no float math on device.
    expect(data.totals.committedAmountDisplay, '2000.0');
    expect(data.openingBalanceDisplay, '10000.0');
    expect(data.openingBalanceSource, 'settings');
    expect(data.positionPeriods, hasLength(1));
    expect(data.projectedEndBalanceDisplay, '7000.0');
    expect(data.hasBreach, isFalse);
  });

  test('surfaces breaches when a period drops below the threshold', () async {
    ApiClient().debugConfigure(
      client: _client(
        position: _positionBody(
          periods: [
            {
              'period': '2026-W30',
              'opening': 6000.0,
              'outflow': 4000.0,
              'inflow': 0.0,
              'closing': 2000.0,
              'below_threshold': true,
            },
          ],
          breaches: [
            {
              'period': '2026-W30',
              'closing': 2000.0,
              'shortfall': 3000.0,
            },
          ],
        ),
      ),
    );

    await store.fetch();

    expect(store.data!.hasBreach, isTrue);
    expect(store.data!.breaches.first.shortfallDisplay, '3000.0');
    expect(store.data!.positionPeriods.first.belowThreshold, isTrue);
  });

  test('surfaces an error and clears data when the network fails', () async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => throw Exception('offline')),
    );

    await store.fetch();

    expect(store.error, isNotNull);
    expect(store.data, isNull);
  });

  test('setHorizon refetches with the new horizon param', () async {
    var lastHorizon = '';
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        lastHorizon = req.url.queryParameters['horizon_days'] ?? '';
        if (req.url.path.endsWith('/analytics/cashflow_forecast')) {
          return _json(_forecastBody(horizonDays: 30));
        }
        return _json(_positionBody());
      }),
    );

    await store.setHorizon(30);

    expect(store.horizonDays, 30);
    expect(lastHorizon, '30');
    expect(store.data, isNotNull);
  });

  test('setHorizon no-ops when the horizon is unchanged', () async {
    var calls = 0;
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        calls++;
        if (req.url.path.endsWith('/analytics/cashflow_forecast')) {
          return _json(_forecastBody());
        }
        return _json(_positionBody());
      }),
    );

    // default horizon is 90 — selecting it again must not hit the API.
    await store.setHorizon(90);

    expect(calls, 0);
    expect(store.data, isNull);
  });
}
