import 'package:flutter_test/flutter_test.dart';

import 'package:feohledger_mobile/models/payment.dart';
import 'package:feohledger_mobile/models/payment_queue.dart';

void main() {
  group('moneyToDisplay', () {
    test('passes a string through verbatim (no float round-trip)', () {
      expect(moneyToDisplay('1500.55'), '1500.55');
    });

    test('renders a num as its string form', () {
      expect(moneyToDisplay(1500), '1500');
      expect(moneyToDisplay(1500.5), '1500.5');
    });

    test('null becomes 0', () {
      expect(moneyToDisplay(null), '0');
    });
  });

  group('PaymentQueueItem.fromJson', () {
    test('parses an eligible row with discount', () {
      final item = PaymentQueueItem.fromJson({
        'id': 'inv1',
        'invoice_number': 'INV-001',
        'vendor_name': 'Acme',
        'amount': 1500.0,
        'currency': 'USD',
        'due_date': '2026-02-01',
        'payment_terms': 'Net 30',
        'status': 'approved',
        'is_overdue': false,
        'discount_eligible': true,
        'discount_date': '2026-01-20',
        'discount_amount': 30.0,
      });
      expect(item.id, 'inv1');
      expect(item.vendorName, 'Acme');
      // Money carried as a display string, never re-parsed into a float.
      expect(item.amountDisplay, '1500.0');
      expect(item.discountEligible, isTrue);
      expect(item.discountAmountDisplay, '30.0');
      expect(item.dueDate, DateTime(2026, 2, 1));
    });

    test('handles a non-eligible row with null discount + no due date', () {
      final item = PaymentQueueItem.fromJson({
        'id': 'inv2',
        'invoice_number': 'INV-002',
        'vendor_name': 'Beta',
        'amount': 200,
        'currency': 'USD',
        'due_date': null,
        'status': 'approved',
        'is_overdue': true,
        'discount_eligible': false,
        'discount_amount': null,
      });
      expect(item.dueDate, isNull);
      expect(item.discountAmountDisplay, isNull);
      expect(item.isOverdue, isTrue);
    });
  });

  group('PaymentSummary.fromJson', () {
    test('parses the KPI bar payload', () {
      final s = PaymentSummary.fromJson({
        'total_paid': 12000.0,
        'total_pending': 3400.0,
        'payment_count': 9,
        'total_rebates': 45.5,
        'queue_count': 4,
      });
      expect(s.totalPaidDisplay, '12000.0');
      expect(s.totalPendingDisplay, '3400.0');
      expect(s.paymentCount, 9);
      expect(s.totalRebatesDisplay, '45.5');
      expect(s.queueCount, 4);
    });
  });

  // These fixtures mirror `PaymentRunResponse` in
  // `backend/app/schemas/payment.py` FIELD FOR FIELD — the shape
  // `GET /api/payments/runs/` actually emits. The previous fixtures were
  // hand-written and carried only the keys the model happened to read, which is
  // how `requires_cfo_approval` stayed "covered" while the response schema
  // declared no such field and FastAPI silently stripped it from every row.
  Map<String, dynamic> runResponse({
    String id = 'run1',
    String status = 'draft',
    double totalAmount = 5000.0,
    int paymentCount = 3,
    bool requiresCfoApproval = false,
    String? cfoApprovedAt,
    String? executedAt,
  }) =>
      {
        'id': id,
        'status': status,
        // OptionalMoneyAmount serialises as a JSON number.
        'total_amount': totalAmount,
        'initiated_by': 'u1',
        'executed_at': executedAt,
        'created_at': '2026-01-10T12:00:00',
        'payment_count': paymentCount,
        'payments_completed': 0,
        'payments_failed': 0,
        'payments_in_flight': 0,
        'payments_pending': paymentCount,
        'requires_cfo_approval': requiresCfoApproval,
        'cfo_approved_at': cfoApprovedAt,
      };

  group('PaymentRun.fromJson', () {
    test('parses a draft run and derives isExecutable + cfoApproved', () {
      final run = PaymentRun.fromJson(
        runResponse(requiresCfoApproval: true),
      );
      expect(run.status, 'draft');
      expect(run.totalAmountDisplay, '5000.0');
      expect(run.paymentCount, 3);
      expect(run.requiresCfoApproval, isTrue);
      expect(run.cfoApproved, isFalse);
      expect(run.isExecutable, isTrue);
    });

    test('a run under the threshold needs no CFO sign-off', () {
      final run = PaymentRun.fromJson(runResponse());
      expect(run.requiresCfoApproval, isFalse);
      expect(run.cfoApproved, isFalse);
    });

    test('a completed run is not executable; cfoApproved reflects the stamp',
        () {
      final run = PaymentRun.fromJson(runResponse(
        id: 'run2',
        status: 'completed',
        totalAmount: 100,
        paymentCount: 1,
        requiresCfoApproval: true,
        cfoApprovedAt: '2026-01-11T09:00:00',
        executedAt: '2026-01-11T10:00:00',
      ));
      expect(run.isExecutable, isFalse);
      expect(run.cfoApproved, isTrue);
      expect(run.executedAt, DateTime(2026, 1, 11, 10));
    });

    test('an older server that omits the CFO fields parses without throwing',
        () {
      // Fail-safe, not fail-secure: the gate is enforced server-side, so a
      // missing field means the pre-flight hint is skipped and the 403 (now
      // rendered as the server's own sentence) is what the user sees.
      final json = runResponse()
        ..remove('requires_cfo_approval')
        ..remove('cfo_approved_at');
      final run = PaymentRun.fromJson(json);
      expect(run.requiresCfoApproval, isFalse);
      expect(run.cfoApproved, isFalse);
    });
  });

  group('PaymentRunSelection', () {
    test('serializes invoice id + method value', () {
      const sel = PaymentRunSelection(
        invoiceId: 'inv1',
        method: PaymentMethod.wire,
      );
      expect(sel.toJson(), {'invoice_id': 'inv1', 'method': 'wire'});
    });
  });
}
