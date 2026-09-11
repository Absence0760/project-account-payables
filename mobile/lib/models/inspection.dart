/// Quality inspections — the 4th leg of 4-way matching (invoice vs PO vs goods
/// receipt vs inspection).
///
/// Mirrors `backend/app/api/inspections.py::_serialize`. Nothing here is money:
/// `accepted_quantity` / `rejected_quantity` are counts of goods stored in a
/// `Numeric(12, 4)` column, so the `Decimal`-only money invariant does not
/// apply to them — but they are still never parsed into a `double` on the way
/// *up*, because the API takes them as strings and Pydantic parses those
/// straight into the column (see `InspectionDraft.toJson`).
library;

/// The outcome vocabulary `po_matching` acts on.
///
/// Mirrors `backend/app/schemas/inspection.py::VALID_RESULTS` — the create
/// endpoint validates against that set and 400s on anything else, so the record
/// form must not offer a fourth option.
///
/// [unknown] exists for *reads* only: `quality_inspections.result` is a
/// free-form `String(20)` and a row can also arrive from a QMS sync, so an
/// unrecognised value renders as a neutral badge rather than being mis-tinted
/// as a pass. It is never offered as a choice.
enum InspectionResult {
  pass('pass'),
  fail('fail'),
  partial('partial'),
  unknown('');

  const InspectionResult(this.value);

  final String value;

  /// The three the record form offers, cleanest outcome first — the same order
  /// the web modal's radio group uses.
  static const selectable = <InspectionResult>[pass, fail, partial];

  static InspectionResult fromString(String? raw) {
    for (final r in InspectionResult.values) {
      if (r != unknown && r.value == raw) return r;
    }
    return unknown;
  }

  /// A `fail` or `partial` is what changes the 4-way match (and can put a
  /// quality hold on a payable invoice); a `pass` leaves it alone.
  bool get changesMatch => this == fail || this == partial;
}

/// Render a `Numeric(12, 4)` quantity the API sent as a JSON number.
///
/// The value is only ever displayed, so it is stringified rather than computed
/// with. A whole count reads as `12`, not `12.0`.
String? quantityToDisplay(Object? raw) {
  if (raw == null) return null;
  if (raw is String) return raw.isEmpty ? null : raw;
  if (raw is num) {
    return raw == raw.roundToDouble() ? raw.toInt().toString() : raw.toString();
  }
  return raw.toString();
}

/// One inspection row, as the list, the detail and the create response all
/// return it (the three shapes are identical server-side, `gr_number`
/// included).
class Inspection {
  final String id;
  final String inspectionNumber;
  final String? poId;
  final String? grId;

  /// The goods receipt's human-readable number, resolved server-side by an
  /// outer join. `null` means the inspection is tied to **no** receipt — a
  /// different fact from "linked to a receipt we could not name", which is why
  /// the UI renders it as "Not linked" with its own explanation: `po_matching`
  /// only ever reads an inspection through a receipt (`gr_id`) or a PO-level
  /// row, so an unlinked one is invisible to the match it was recorded for.
  final String? grNumber;
  final InspectionResult result;
  final DateTime? inspectedDate;
  final String? inspector;
  final String? acceptedQuantityDisplay;
  final String? rejectedQuantityDisplay;
  final String? deviationNotes;
  final String status;
  final DateTime? createdAt;

  const Inspection({
    required this.id,
    required this.inspectionNumber,
    this.poId,
    this.grId,
    this.grNumber,
    required this.result,
    this.inspectedDate,
    this.inspector,
    this.acceptedQuantityDisplay,
    this.rejectedQuantityDisplay,
    this.deviationNotes,
    required this.status,
    this.createdAt,
  });

  /// True when the row names neither a receipt nor a purchase order, so no
  /// match will ever read it. The screen surfaces this rather than leaving a
  /// blank cell.
  bool get isUnlinked => grId == null && poId == null;

  factory Inspection.fromJson(Map<String, dynamic> json) {
    return Inspection(
      id: json['id'] as String? ?? '',
      inspectionNumber: json['inspection_number'] as String? ?? '',
      poId: json['po_id'] as String?,
      grId: json['gr_id'] as String?,
      grNumber: json['gr_number'] as String?,
      result: InspectionResult.fromString(json['result'] as String?),
      inspectedDate: DateTime.tryParse(json['inspected_date'] as String? ?? ''),
      inspector: json['inspector'] as String?,
      acceptedQuantityDisplay: quantityToDisplay(json['accepted_quantity']),
      rejectedQuantityDisplay: quantityToDisplay(json['rejected_quantity']),
      deviationNotes: json['deviation_notes'] as String?,
      status: json['status'] as String? ?? '',
      createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
    );
  }
}

/// The `POST /api/inspections` body.
///
/// Quantities are carried as the **raw text the inspector typed** and sent as
/// strings: Pydantic parses those straight into the `Decimal(12, 4)` the column
/// stores, so the digits reach the database untouched. Routing them through a
/// Dart `double` first would be the one lossy step in the chain, and
/// `accepted_quantity` is read by a human downstream — the matcher renders it
/// into its "Partial acceptance: N of ordered quantity accepted" issue.
class InspectionDraft {
  final String inspectionNumber;

  /// The receipt the inspection covers. Required by *this* form even though the
  /// API accepts a body with neither `gr_id` nor `po_id`: `po_matching` reads an
  /// inspection only through the matched receipt's `gr_id`, or a PO-level row
  /// whose `gr_id` is NULL. Offering an unlinked row as an option would let
  /// someone record a failed inspection, see it listed, and watch the invoice
  /// pay anyway. Same reasoning as the web `RecordInspectionModal`.
  final String grId;

  /// The receipt's PO, when it has one — sent alongside so a PO-level match can
  /// read the row too.
  final String? poId;
  final InspectionResult result;
  final DateTime? inspectedDate;
  final String? inspector;
  final String? acceptedQuantity;
  final String? rejectedQuantity;
  final String? deviationNotes;

  const InspectionDraft({
    required this.inspectionNumber,
    required this.grId,
    this.poId,
    required this.result,
    this.inspectedDate,
    this.inspector,
    this.acceptedQuantity,
    this.rejectedQuantity,
    this.deviationNotes,
  });

  Map<String, dynamic> toJson() {
    final body = <String, dynamic>{
      'inspection_number': inspectionNumber,
      'gr_id': grId,
      'result': result.value,
    };
    if (poId != null) body['po_id'] = poId;
    if (inspectedDate != null) {
      final d = inspectedDate!;
      body['inspected_date'] = '${d.year.toString().padLeft(4, '0')}-'
          '${d.month.toString().padLeft(2, '0')}-'
          '${d.day.toString().padLeft(2, '0')}';
    }
    if (inspector != null && inspector!.isNotEmpty) {
      body['inspector'] = inspector;
    }
    if (acceptedQuantity != null && acceptedQuantity!.isNotEmpty) {
      body['accepted_quantity'] = acceptedQuantity;
    }
    if (rejectedQuantity != null && rejectedQuantity!.isNotEmpty) {
      body['rejected_quantity'] = rejectedQuantity;
    }
    if (deviationNotes != null && deviationNotes!.isNotEmpty) {
      body['deviation_notes'] = deviationNotes;
    }
    return body;
  }
}
