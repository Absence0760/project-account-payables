import 'package:flutter_test/flutter_test.dart';

import 'package:ap_mobile/models/invoice.dart';

void main() {
  group('InvoiceStatus', () {
    test('fromString maps every known wire value', () {
      for (final status in InvoiceStatus.values) {
        expect(InvoiceStatus.fromString(status.value), status);
      }
    });

    test('fromString falls back to newStatus for unknown values', () {
      expect(InvoiceStatus.fromString('not_a_status'), InvoiceStatus.newStatus);
      expect(InvoiceStatus.fromString(''), InvoiceStatus.newStatus);
    });

    test('every status has a human label', () {
      for (final status in InvoiceStatus.values) {
        expect(status.label, isNotEmpty);
      }
      expect(InvoiceStatus.readyForReview.label, 'Ready for Review');
      expect(InvoiceStatus.paymentScheduled.label, 'Payment Scheduled');
    });

    test('isActionable is true only for ready_for_review', () {
      expect(InvoiceStatus.readyForReview.isActionable, isTrue);
      for (final status in InvoiceStatus.values) {
        if (status != InvoiceStatus.readyForReview) {
          expect(status.isActionable, isFalse, reason: status.value);
        }
      }
    });
  });

  group('Invoice.fromJson', () {
    test('parses a fully-populated invoice', () {
      final invoice = Invoice.fromJson({
        'id': 'inv1',
        'invoice_number': 'INV-001',
        'vendor_name': 'Acme Supplies',
        'amount': 1234.56,
        'currency': 'EUR',
        'status': 'approved',
        'invoice_date': '2026-01-01T00:00:00',
        'due_date': '2026-02-01T00:00:00',
        'description': 'Widgets',
        'po_number': 'PO-9',
        'file_url': 'https://x/inv1.pdf',
        'created_at': '2026-01-01T12:00:00',
      });

      expect(invoice.id, 'inv1');
      expect(invoice.invoiceNumber, 'INV-001');
      expect(invoice.vendorName, 'Acme Supplies');
      expect(invoice.amount, 1234.56);
      expect(invoice.currency, 'EUR');
      expect(invoice.status, InvoiceStatus.approved);
      expect(invoice.invoiceDate, DateTime.parse('2026-01-01T00:00:00'));
      expect(invoice.dueDate, DateTime.parse('2026-02-01T00:00:00'));
      expect(invoice.description, 'Widgets');
      expect(invoice.poNumber, 'PO-9');
      expect(invoice.fileUrl, 'https://x/inv1.pdf');
    });

    test('falls back to the "vendor" key when "vendor_name" is absent', () {
      final invoice = Invoice.fromJson({
        'id': 'inv1',
        'vendor': 'Legacy Vendor',
        'status': 'new',
        'created_at': '2026-01-01T12:00:00',
      });
      expect(invoice.vendorName, 'Legacy Vendor');
    });

    test('defaults currency to USD and coerces integer amount to double', () {
      final invoice = Invoice.fromJson({
        'id': 'inv1',
        'amount': 100, // integer from JSON
        'status': 'pending',
        'created_at': '2026-01-01T12:00:00',
      });
      expect(invoice.currency, 'USD');
      expect(invoice.amount, 100.0);
      expect(invoice.amount, isA<double>());
    });

    test('leaves optional fields null when missing', () {
      final invoice = Invoice.fromJson({
        'id': 'inv1',
        'status': 'pending',
        'created_at': '2026-01-01T12:00:00',
      });
      expect(invoice.invoiceNumber, isNull);
      expect(invoice.vendorName, isNull);
      expect(invoice.amount, isNull);
      expect(invoice.invoiceDate, isNull);
      expect(invoice.dueDate, isNull);
      expect(invoice.poNumber, isNull);
      expect(invoice.fileUrl, isNull);
    });

    test('unknown status string degrades to newStatus rather than throwing', () {
      final invoice = Invoice.fromJson({
        'id': 'inv1',
        'status': 'bogus',
        'created_at': '2026-01-01T12:00:00',
      });
      expect(invoice.status, InvoiceStatus.newStatus);
    });

    test('parses warnings into typed InvoiceWarning list', () {
      final invoice = Invoice.fromJson({
        'id': 'inv1',
        'status': 'ready_for_review',
        'created_at': '2026-01-01T12:00:00',
        'warnings': [
          {'type': 'duplicate', 'severity': 'warning', 'message': 'Dup'},
          {'type': 'missing_field', 'severity': 'error', 'message': 'No amount'},
          {'type': 'fraud_round_amount', 'severity': 'info', 'message': 'Round'},
        ],
      });
      expect(invoice.warnings, hasLength(3));
      expect(invoice.warnings[0].type, 'duplicate');
      expect(invoice.warnings[0].severity, WarningSeverity.warning);
      expect(invoice.warnings[1].severity, WarningSeverity.error);
      expect(invoice.warnings[2].severity, WarningSeverity.info);
      expect(invoice.warnings[1].message, 'No amount');
    });

    test('defaults to an empty warnings list when absent or not a list', () {
      expect(
        Invoice.fromJson({
          'id': 'i',
          'status': 'new',
          'created_at': '2026-01-01T12:00:00',
        }).warnings,
        isEmpty,
      );
      expect(
        Invoice.fromJson({
          'id': 'i',
          'status': 'new',
          'created_at': '2026-01-01T12:00:00',
          'warnings': 'not-a-list',
        }).warnings,
        isEmpty,
      );
    });

    test('warning severity falls back to info on an unknown value', () {
      final w = InvoiceWarning.fromJson(
        {'type': 'x', 'severity': 'bogus', 'message': 'm'},
      );
      expect(w.severity, WarningSeverity.info);
    });

    test('parses po_match into a typed PoMatch', () {
      final invoice = Invoice.fromJson({
        'id': 'inv1',
        'status': 'ready_for_review',
        'created_at': '2026-01-01T12:00:00',
        'po_match': {
          'match_type': '3-way',
          'status': 'mismatch',
          'variance_pct': 12.5,
          'within_tolerance': false,
          'issues': ['Amount variance', 'Quantity mismatch'],
        },
      });
      final m = invoice.poMatch!;
      expect(m.matchType, '3-way');
      expect(m.status, 'mismatch');
      expect(m.statusLabel, 'Mismatch');
      expect(m.variancePct, 12.5);
      expect(m.withinTolerance, isFalse);
      expect(m.issues, ['Amount variance', 'Quantity mismatch']);
      expect(m.isNoPo, isFalse);
    });

    test('po_match is null when absent, isNoPo when status=no_po', () {
      expect(
        Invoice.fromJson({
          'id': 'i',
          'status': 'new',
          'created_at': '2026-01-01T12:00:00',
        }).poMatch,
        isNull,
      );
      final noPo = Invoice.fromJson({
        'id': 'i',
        'status': 'new',
        'created_at': '2026-01-01T12:00:00',
        'po_match': {'match_type': 'none', 'status': 'no_po', 'issues': []},
      }).poMatch!;
      expect(noPo.isNoPo, isTrue);
    });
  });
}
