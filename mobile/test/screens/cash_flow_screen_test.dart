import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/screens/cash_flow_screen.dart';
import 'package:ap_mobile/stores/cash_flow_store.dart';
import 'package:ap_mobile/widgets/kpi_card.dart';

http.Response _json(Object body, [int status = 200]) => http.Response(
      jsonEncode(body),
      status,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _forecastBody() => {
      'granularity': 'week',
      'horizon_days': 90,
      'periods': [
        {
          'period': '2026-W26',
          'scheduled_amount': 3000.0,
          'committed_amount': 2000.0,
          'pending_amount': 1000.0,
          'discount_eligible_amount': 0.0,
          'count': 4,
        },
      ],
      'totals': {
        'scheduled_amount': 3000.0,
        'committed_amount': 2000.0,
        'pending_amount': 1000.0,
        'discount_eligible_amount': 0.0,
        'count': 4,
      },
    };

Map<String, dynamic> _positionBody({
  List<Map<String, dynamic>>? breaches,
  bool below = false,
}) =>
    {
      'granularity': 'week',
      'horizon_days': 90,
      'opening_balance': 10000.0,
      'opening_balance_source': 'settings',
      'threshold': 5000.0,
      'periods': [
        {
          'period': '2026-W26',
          'opening': 10000.0,
          'outflow': 3000.0,
          'inflow': 0.0,
          'closing': 7000.0,
          'below_threshold': below,
        },
      ],
      'breaches': breaches ?? [],
    };

MockClient _client({Map<String, dynamic>? position}) => MockClient((req) async {
      if (req.url.path.endsWith('/analytics/cashflow_forecast')) {
        return _json(_forecastBody());
      }
      if (req.url.path.endsWith('/analytics/cash_position')) {
        return _json(position ?? _positionBody());
      }
      return http.Response('not found', 404);
    });

Future<void> _pumpUntil(
  WidgetTester tester,
  Finder finder, {
  int maxFrames = 20,
}) async {
  for (var i = 0; i < maxFrames && finder.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  setUp(() {
    CashFlowStore.instance.debugReset();
    ApiClient().debugConfigure();
  });

  testWidgets('shows a spinner while loading', (tester) async {
    final gate = Completer<http.Response>();
    ApiClient().debugConfigure(client: MockClient((req) async => gate.future));

    await tester.pumpWidget(const MaterialApp(home: CashFlowScreen()));
    await tester.pump();
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    // Settle the in-flight request into an error so no Timer leaks, leaving
    // _data null for downstream tests.
    gate.completeError(Exception('offline'));
    await _pumpUntil(tester, find.text('Retry'));
  });

  testWidgets('shows an error state with Retry when fetch fails', (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => throw Exception('offline')),
    );

    await tester.pumpWidget(const MaterialApp(home: CashFlowScreen()));
    await _pumpUntil(tester, find.text('Retry'));

    expect(find.textContaining('Error:'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Retry'), findsOneWidget);
    expect(find.byType(KpiCard), findsNothing);
  });

  testWidgets('renders the KPI summary and forecast/position rows', (tester) async {
    ApiClient().debugConfigure(client: _client());

    await tester.pumpWidget(const MaterialApp(home: CashFlowScreen()));
    await _pumpUntil(tester, find.byType(KpiCard));

    // Four KPI cards: Opening, Projected End, Committed Out, Pending Out.
    expect(find.byType(KpiCard), findsNWidgets(4));
    expect(find.text('Opening Balance'), findsOneWidget);
    expect(find.text('Projected End'), findsOneWidget);
    expect(find.text('Committed Out'), findsOneWidget);
    expect(find.text('Pending Out'), findsOneWidget);

    // Section headers + a forecast/position row keyed on the period label.
    expect(find.text('Projected Outflows'), findsOneWidget);
    expect(find.text('Cash Position'), findsOneWidget);
    expect(find.text('2026-W26'), findsNWidgets(2)); // forecast + position rows

    // Server-computed money rendered (opening 10000 is the unique KPI value;
    // 7000 is the projected-end KPI AND the single position-row closing).
    expect(find.text('\$10,000.00'), findsOneWidget);
    expect(find.text('\$7,000.00'), findsNWidgets(2));

    // No breach -> no low-balance alert.
    expect(find.text('Low balance alert'), findsNothing);
  });

  testWidgets('surfaces a low-balance alert when a period breaches the threshold',
      (tester) async {
    ApiClient().debugConfigure(
      client: _client(
        position: _positionBody(
          below: true,
          breaches: [
            {'period': '2026-W26', 'closing': 2000.0, 'shortfall': 3000.0},
          ],
        ),
      ),
    );

    await tester.pumpWidget(const MaterialApp(home: CashFlowScreen()));
    await _pumpUntil(tester, find.text('Low balance alert'));

    expect(find.text('Low balance alert'), findsOneWidget);
    // The alert names the shortfall (server-computed).
    expect(find.textContaining('\$3,000.00'), findsWidgets);
  });

  testWidgets('renders empty-state copy when there are no periods', (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.url.path.endsWith('/analytics/cashflow_forecast')) {
          return _json({
            'granularity': 'week',
            'horizon_days': 90,
            'periods': <Map<String, dynamic>>[],
            'totals': {
              'scheduled_amount': 0.0,
              'committed_amount': 0.0,
              'pending_amount': 0.0,
              'discount_eligible_amount': 0.0,
              'count': 0,
            },
          });
        }
        return _json({
          'granularity': 'week',
          'horizon_days': 90,
          'opening_balance': 0.0,
          'opening_balance_source': 'none',
          'threshold': null,
          'periods': <Map<String, dynamic>>[],
          'breaches': <Map<String, dynamic>>[],
        });
      }),
    );

    await tester.pumpWidget(const MaterialApp(home: CashFlowScreen()));
    await _pumpUntil(tester, find.byType(KpiCard));

    expect(find.text('No projected outflows in this horizon.'), findsOneWidget);
    expect(
      find.text('No cash-position projection for this horizon.'),
      findsOneWidget,
    );
  });

  testWidgets('tapping a horizon chip refetches with the new horizon',
      (tester) async {
    final horizons = <String>[];
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.url.path.endsWith('/analytics/cashflow_forecast')) {
          horizons.add(req.url.queryParameters['horizon_days'] ?? '');
          return _json(_forecastBody());
        }
        return _json(_positionBody());
      }),
    );

    await tester.pumpWidget(const MaterialApp(home: CashFlowScreen()));
    await _pumpUntil(tester, find.byType(KpiCard));
    expect(horizons, contains('90')); // initial fetch

    await tester.tap(find.text('30 days'));
    await _pumpUntil(tester, find.byType(KpiCard));

    expect(horizons, contains('30'));
    expect(CashFlowStore.instance.horizonDays, 30);
  });
}
