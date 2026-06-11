import 'package:flutter_test/flutter_test.dart';

import 'package:ap_mobile/models/payment.dart';

void main() {
  group('PaymentMethod / PaymentStatus enums', () {
    test('fromString round-trips known values', () {
      for (final m in PaymentMethod.values) {
        expect(PaymentMethod.fromString(m.value), m);
      }
      for (final s in PaymentStatus.values) {
        expect(PaymentStatus.fromString(s.value), s);
      }
    });

    test('fromString falls back to a sane default for unknown values', () {
      expect(PaymentMethod.fromString('bitcoin'), PaymentMethod.ach);
      expect(PaymentStatus.fromString('???'), PaymentStatus.pending);
    });

    test('payment method labels are human-readable', () {
      expect(PaymentMethod.ach.label, 'ACH');
      expect(PaymentMethod.virtualCard.label, 'Virtual Card');
    });
  });

  group('Payment.fromJson', () {
    test('parses a payment and coerces amount to double', () {
      final payment = Payment.fromJson({
        'id': 'p1',
        'invoice_id': 'inv1',
        'amount': 500, // integer
        'method': 'wire',
        'status': 'completed',
        'reference': 'WIRE-1',
        'created_at': '2026-01-01T12:00:00',
      });
      expect(payment.id, 'p1');
      expect(payment.invoiceId, 'inv1');
      expect(payment.amount, 500.0);
      expect(payment.method, PaymentMethod.wire);
      expect(payment.status, PaymentStatus.completed);
      expect(payment.reference, 'WIRE-1');
    });

    test('reference is optional', () {
      final payment = Payment.fromJson({
        'id': 'p1',
        'invoice_id': 'inv1',
        'amount': 1.0,
        'method': 'ach',
        'status': 'pending',
        'created_at': '2026-01-01T12:00:00',
      });
      expect(payment.reference, isNull);
    });
  });

  group('DashboardData.fromJson', () {
    test('parses a full dashboard payload', () {
      final data = DashboardData.fromJson({
        'total_invoices': 12,
        'total_amount': 9876.54,
        'pipeline': {'pending': 3, 'approved': 2},
        'vendor_spend': [
          {'vendor': 'Acme', 'amount': 1000, 'invoice_count': 4},
        ],
        'aging': {
          'current': 100,
          'days_30': 200,
          'days_60': 300,
          'days_90_plus': 400,
        },
        'monthly_trend': [
          {'month': '2026-01', 'count': 5, 'amount': 500},
        ],
        'upcoming_payments': [
          {'amount': 250},
          {'amount': 250},
        ],
      });

      expect(data.totalInvoices, 12);
      expect(data.totalAmount, 9876.54);
      expect(data.pipeline, {'pending': 3, 'approved': 2});

      expect(data.topVendors, hasLength(1));
      expect(data.topVendors.first.vendorName, 'Acme');
      expect(data.topVendors.first.totalAmount, 1000.0);

      expect(data.aging.current, 100.0);
      expect(data.aging.thirtyDays, 200.0);
      expect(data.aging.sixtyDays, 300.0);
      expect(data.aging.ninetyPlus, 400.0);

      expect(data.trends, hasLength(1));
      expect(data.trends.first.month, '2026-01');

      // upcoming is summarized from the list of upcoming invoices
      expect(data.upcoming.count, 2);
      expect(data.upcoming.totalAmount, 500.0);
    });

    test('tolerates a completely empty payload with safe defaults', () {
      final data = DashboardData.fromJson({});
      expect(data.totalInvoices, 0);
      expect(data.totalAmount, 0);
      expect(data.pipeline, isEmpty);
      expect(data.topVendors, isEmpty);
      expect(data.trends, isEmpty);
      expect(data.aging.current, 0);
      expect(data.upcoming.count, 0);
      expect(data.upcoming.totalAmount, 0);
    });

    test('aging accepts the alternate key spellings', () {
      final data = DashboardData.fromJson({
        'aging': {
          'current': 1,
          '30_days': 2,
          '60_days': 3,
          '90_plus': 4,
        },
      });
      expect(data.aging.thirtyDays, 2.0);
      expect(data.aging.sixtyDays, 3.0);
      expect(data.aging.ninetyPlus, 4.0);
    });

    test('vendor_spend accepts vendor_name/total_amount spellings', () {
      final data = DashboardData.fromJson({
        'vendor_spend': [
          {'vendor_name': 'Beta', 'total_amount': 42, 'invoice_count': 1},
        ],
      });
      expect(data.topVendors.first.vendorName, 'Beta');
      expect(data.topVendors.first.totalAmount, 42.0);
    });
  });
}
