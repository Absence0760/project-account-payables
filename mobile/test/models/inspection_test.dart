import 'package:flutter_test/flutter_test.dart';

import 'package:feohledger_mobile/models/inspection.dart';

void main() {
  group('InspectionResult', () {
    test('parses the three the backend validates against', () {
      expect(InspectionResult.fromString('pass'), InspectionResult.pass);
      expect(InspectionResult.fromString('fail'), InspectionResult.fail);
      expect(InspectionResult.fromString('partial'), InspectionResult.partial);
    });

    test('an unrecognised or missing value is unknown, never a pass', () {
      // `quality_inspections.result` is a free-form String(20) and a row can
      // arrive from a QMS sync, so a value outside the vocabulary has to be
      // nameable — rendering it as a pass would be a wrong answer about whether
      // goods were accepted.
      expect(InspectionResult.fromString('quarantined'), InspectionResult.unknown);
      expect(InspectionResult.fromString(null), InspectionResult.unknown);
      expect(InspectionResult.fromString(''), InspectionResult.unknown);
    });

    test('only the three are offered as choices', () {
      expect(InspectionResult.selectable, [
        InspectionResult.pass,
        InspectionResult.fail,
        InspectionResult.partial,
      ]);
      expect(
        InspectionResult.selectable.contains(InspectionResult.unknown),
        isFalse,
      );
    });

    test('fail and partial are the outcomes that change the match', () {
      expect(InspectionResult.pass.changesMatch, isFalse);
      expect(InspectionResult.fail.changesMatch, isTrue);
      expect(InspectionResult.partial.changesMatch, isTrue);
    });
  });

  group('quantityToDisplay', () {
    test('a whole count renders without a decimal tail', () {
      // The backend float()s its Numeric(12, 4) columns, so 12 arrives as 12.0.
      expect(quantityToDisplay(12.0), '12');
      expect(quantityToDisplay(12), '12');
    });

    test('a fractional quantity keeps its digits', () {
      expect(quantityToDisplay(12.5), '12.5');
      expect(quantityToDisplay(0.25), '0.25');
    });

    test('a string passes through and an absent value stays null', () {
      expect(quantityToDisplay('7.2500'), '7.2500');
      expect(quantityToDisplay(null), isNull);
      expect(quantityToDisplay(''), isNull);
    });
  });

  group('Inspection.fromJson', () {
    test('maps every field the serializer returns', () {
      final qi = Inspection.fromJson({
        'id': 'qi1',
        'inspection_number': 'QI-GR-1',
        'po_id': 'po1',
        'gr_id': 'gr1',
        'gr_number': 'GR-1',
        'result': 'partial',
        'inspected_date': '2026-03-04',
        'inspector': 'Dana',
        'accepted_quantity': 8.0,
        'rejected_quantity': 2.5,
        'deviation_notes': 'Two cartons crushed',
        'status': 'completed',
        'created_at': '2026-03-04T09:30:00',
      });

      expect(qi.id, 'qi1');
      expect(qi.inspectionNumber, 'QI-GR-1');
      expect(qi.grNumber, 'GR-1');
      expect(qi.result, InspectionResult.partial);
      expect(qi.inspectedDate, DateTime(2026, 3, 4));
      expect(qi.inspector, 'Dana');
      expect(qi.acceptedQuantityDisplay, '8');
      expect(qi.rejectedQuantityDisplay, '2.5');
      expect(qi.deviationNotes, 'Two cartons crushed');
      expect(qi.status, 'completed');
      expect(qi.isUnlinked, isFalse);
    });

    test('a row naming neither a receipt nor a PO is unlinked', () {
      // The distinction the UI renders: `po_matching` reads an inspection only
      // through a receipt or a PO-level row, so this one changes nothing.
      final qi = Inspection.fromJson({
        'id': 'qi2',
        'inspection_number': 'QI-ORPHAN',
        'po_id': null,
        'gr_id': null,
        'gr_number': null,
        'result': 'fail',
        'status': 'completed',
      });

      expect(qi.isUnlinked, isTrue);
      expect(qi.grNumber, isNull);
    });

    test('a receipt-linked row the join could not name is NOT unlinked', () {
      final qi = Inspection.fromJson({
        'id': 'qi3',
        'inspection_number': 'QI-3',
        'gr_id': 'gr9',
        'gr_number': null,
        'result': 'pass',
        'status': 'completed',
      });

      expect(qi.isUnlinked, isFalse);
      expect(qi.grNumber, isNull);
    });
  });

  group('InspectionDraft.toJson', () {
    test('quantities go up as STRINGS, exactly as typed', () {
      // Pydantic parses the string straight into Decimal(12, 4); routing it
      // through a Dart double first would be the one lossy step in the chain.
      final body = InspectionDraft(
        inspectionNumber: 'QI-1',
        grId: 'gr1',
        poId: 'po1',
        result: InspectionResult.partial,
        acceptedQuantity: '8.2500',
        rejectedQuantity: '1.7500',
      ).toJson();

      expect(body['accepted_quantity'], '8.2500');
      expect(body['rejected_quantity'], '1.7500');
      expect(body['accepted_quantity'], isA<String>());
      expect(body['rejected_quantity'], isA<String>());
    });

    test('sends the receipt and its PO together', () {
      final body = InspectionDraft(
        inspectionNumber: 'QI-1',
        grId: 'gr1',
        poId: 'po1',
        result: InspectionResult.fail,
      ).toJson();

      expect(body['gr_id'], 'gr1');
      expect(body['po_id'], 'po1');
      expect(body['result'], 'fail');
    });

    test('omits every empty optional rather than sending a blank', () {
      final body = InspectionDraft(
        inspectionNumber: 'QI-1',
        grId: 'gr1',
        result: InspectionResult.pass,
        inspector: '',
        acceptedQuantity: '',
        rejectedQuantity: '',
        deviationNotes: '',
      ).toJson();

      expect(body.keys, unorderedEquals(['inspection_number', 'gr_id', 'result']));
    });

    test('the inspected date is sent as YYYY-MM-DD', () {
      final body = InspectionDraft(
        inspectionNumber: 'QI-1',
        grId: 'gr1',
        result: InspectionResult.pass,
        inspectedDate: DateTime(2026, 3, 4),
      ).toJson();

      expect(body['inspected_date'], '2026-03-04');
    });
  });
}
