/// One goods receipt, as `backend/app/api/goods_receipts.py::list_goods_receipts`
/// returns it.
///
/// Mobile reads receipts for exactly one reason: an inspection has to name the
/// delivery it covers, so the record-inspection form needs a picker. There is no
/// receipts *screen* — the 3-way-match detail stays on the web.
class GoodsReceipt {
  final String id;
  final String grNumber;
  final String? poId;

  /// The PO's human-readable number, resolved server-side for the rendered page.
  /// `null` when the receipt is against no PO.
  final String? poNumber;
  final DateTime? receivedDate;
  final String status;
  final int lineCount;

  const GoodsReceipt({
    required this.id,
    required this.grNumber,
    this.poId,
    this.poNumber,
    this.receivedDate,
    required this.status,
    required this.lineCount,
  });

  /// `GR-123 → PO-2026-005`, or the receipt number alone when it has no PO —
  /// the label the picker shows, matching the web modal's option text.
  String get label => poNumber == null ? grNumber : '$grNumber → $poNumber';

  factory GoodsReceipt.fromJson(Map<String, dynamic> json) {
    return GoodsReceipt(
      id: json['id'] as String? ?? '',
      grNumber: json['gr_number'] as String? ?? '',
      poId: json['po_id'] as String?,
      poNumber: json['po_number'] as String?,
      receivedDate: DateTime.tryParse(json['received_date'] as String? ?? ''),
      status: json['status'] as String? ?? '',
      lineCount: (json['line_count'] as num?)?.toInt() ?? 0,
    );
  }
}
