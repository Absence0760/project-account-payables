import 'package:ap_mobile/models/payment.dart';

/// Convert a JSON money value (the backend emits these dict responses with
/// `float(...)`, so they arrive as JSON numbers) into a display string WITHOUT
/// doing any client-side float arithmetic on it. The string is what we render;
/// we never add / multiply money on the device — server-computed totals
/// (`total_amount`) are used for sums. Mirrors the web app's "money as
/// string-Decimal, never client float math" invariant.
String moneyToDisplay(Object? raw) {
  if (raw == null) return '0';
  if (raw is String) return raw;
  // num -> string verbatim; we only ever display this, never compute with it.
  return raw.toString();
}

/// One approved invoice awaiting payment, from `GET /api/payments/queue`.
class PaymentQueueItem {
  final String id;
  final String invoiceNumber;
  final String vendorName;

  /// Display string for the invoice amount. Never used in arithmetic.
  final String amountDisplay;
  final String currency;
  final DateTime? dueDate;
  final String? paymentTerms;
  final String status;
  final bool isOverdue;
  final bool discountEligible;
  final DateTime? discountDate;

  /// Display string for the discount amount, null when not eligible.
  final String? discountAmountDisplay;

  PaymentQueueItem({
    required this.id,
    required this.invoiceNumber,
    required this.vendorName,
    required this.amountDisplay,
    required this.currency,
    this.dueDate,
    this.paymentTerms,
    required this.status,
    required this.isOverdue,
    required this.discountEligible,
    this.discountDate,
    this.discountAmountDisplay,
  });

  factory PaymentQueueItem.fromJson(Map<String, dynamic> json) {
    DateTime? parseDate(Object? v) {
      if (v is String && v.isNotEmpty) return DateTime.tryParse(v);
      return null;
    }

    return PaymentQueueItem(
      id: json['id'] as String,
      invoiceNumber: json['invoice_number'] as String? ?? '',
      vendorName: json['vendor_name'] as String? ?? 'Unknown',
      amountDisplay: moneyToDisplay(json['amount']),
      currency: json['currency'] as String? ?? 'USD',
      dueDate: parseDate(json['due_date']),
      paymentTerms: json['payment_terms'] as String?,
      status: json['status'] as String? ?? 'approved',
      isOverdue: json['is_overdue'] as bool? ?? false,
      discountEligible: json['discount_eligible'] as bool? ?? false,
      discountDate: parseDate(json['discount_date']),
      discountAmountDisplay: json['discount_amount'] == null
          ? null
          : moneyToDisplay(json['discount_amount']),
    );
  }
}

/// Payment-page KPI bar, from `GET /api/payments/summary`. Each value is a
/// server-computed display string; the device never sums money.
class PaymentSummary {
  final String totalPaidDisplay;
  final String totalPendingDisplay;
  final int paymentCount;
  final String totalRebatesDisplay;
  final int queueCount;

  PaymentSummary({
    required this.totalPaidDisplay,
    required this.totalPendingDisplay,
    required this.paymentCount,
    required this.totalRebatesDisplay,
    required this.queueCount,
  });

  factory PaymentSummary.fromJson(Map<String, dynamic> json) {
    return PaymentSummary(
      totalPaidDisplay: moneyToDisplay(json['total_paid']),
      totalPendingDisplay: moneyToDisplay(json['total_pending']),
      paymentCount: json['payment_count'] as int? ?? 0,
      totalRebatesDisplay: moneyToDisplay(json['total_rebates']),
      queueCount: json['queue_count'] as int? ?? 0,
    );
  }
}

/// A batch from `GET /api/payments/runs/`.
class PaymentRun {
  final String id;
  final String status;
  final String totalAmountDisplay;
  final int paymentCount;
  final bool requiresCfoApproval;
  final bool cfoApproved;
  final DateTime createdAt;
  final DateTime? executedAt;

  PaymentRun({
    required this.id,
    required this.status,
    required this.totalAmountDisplay,
    required this.paymentCount,
    required this.requiresCfoApproval,
    required this.cfoApproved,
    required this.createdAt,
    this.executedAt,
  });

  factory PaymentRun.fromJson(Map<String, dynamic> json) {
    DateTime parseRequired(Object? v) =>
        v is String ? DateTime.parse(v) : DateTime.now();
    DateTime? parseOptional(Object? v) =>
        v is String && v.isNotEmpty ? DateTime.tryParse(v) : null;

    return PaymentRun(
      id: json['id'] as String,
      status: json['status'] as String? ?? 'draft',
      totalAmountDisplay: moneyToDisplay(json['total_amount']),
      paymentCount: (json['payment_count'] as int?) ??
          (json['payment_count'] as num?)?.toInt() ??
          0,
      requiresCfoApproval: json['requires_cfo_approval'] as bool? ?? false,
      cfoApproved: json['cfo_approved_at'] != null,
      createdAt: parseRequired(json['created_at']),
      executedAt: parseOptional(json['executed_at']),
    );
  }

  /// Only `draft` runs can be executed (or cancelled) from the app.
  bool get isExecutable => status == 'draft';
}

/// Selection passed to `POST /api/payments/runs` — one invoice + chosen method.
class PaymentRunSelection {
  final String invoiceId;
  final PaymentMethod method;

  const PaymentRunSelection({required this.invoiceId, required this.method});

  Map<String, dynamic> toJson() => {
        'invoice_id': invoiceId,
        'method': method.value,
      };
}
