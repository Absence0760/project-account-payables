/// Exception-queue entity. Mirrors the shape returned by the backend
/// `GET /api/exceptions` list endpoint (see backend `_exception_dict`).
///
/// Named `ApException` to avoid clashing with Dart core `Exception` — the
/// backend ORM model does the same dodge (`Exception as APException`).
enum ApExceptionStatus {
  open('open'),
  escalated('escalated'),
  resolved('resolved'),
  dismissed('dismissed');

  const ApExceptionStatus(this.value);
  final String value;

  static ApExceptionStatus fromString(String s) {
    return ApExceptionStatus.values.firstWhere(
      (e) => e.value == s,
      orElse: () => ApExceptionStatus.open,
    );
  }

  String get label => switch (this) {
    ApExceptionStatus.open => 'Open',
    ApExceptionStatus.escalated => 'Escalated',
    ApExceptionStatus.resolved => 'Resolved',
    ApExceptionStatus.dismissed => 'Dismissed',
  };

  /// Only open / escalated exceptions can be acted on — resolved and dismissed
  /// are terminal (the backend 409s a resolve from any other status).
  bool get isActionable =>
      this == ApExceptionStatus.open || this == ApExceptionStatus.escalated;
}

/// Severity ranks an exception's urgency. Backend values: error / warning / info.
enum ApExceptionSeverity {
  error('error'),
  warning('warning'),
  info('info');

  const ApExceptionSeverity(this.value);
  final String value;

  static ApExceptionSeverity fromString(String? s) {
    return ApExceptionSeverity.values.firstWhere(
      (e) => e.value == s,
      orElse: () => ApExceptionSeverity.info,
    );
  }

  String get label => switch (this) {
    ApExceptionSeverity.error => 'Error',
    ApExceptionSeverity.warning => 'Warning',
    ApExceptionSeverity.info => 'Info',
  };
}

class ApException {
  final String id;
  final String? invoiceId;
  final String? invoiceNumber;
  final String? vendorName;
  final double? amount;
  final String exceptionType;

  /// Human-readable label resolved server-side (e.g. "Duplicate Invoice").
  /// Falls back to the raw type when the backend doesn't map it.
  final String typeLabel;
  final ApExceptionSeverity severity;
  final String? description;
  final ApExceptionStatus status;
  final String? resolution;
  final String? assignedTo;
  final bool isOverdue;
  final DateTime createdAt;

  ApException({
    required this.id,
    this.invoiceId,
    this.invoiceNumber,
    this.vendorName,
    this.amount,
    required this.exceptionType,
    required this.typeLabel,
    required this.severity,
    this.description,
    required this.status,
    this.resolution,
    this.assignedTo,
    this.isOverdue = false,
    required this.createdAt,
  });

  factory ApException.fromJson(Map<String, dynamic> json) {
    final type = json['exception_type'] as String? ?? 'unknown';
    return ApException(
      id: json['id'] as String,
      invoiceId: json['invoice_id'] as String?,
      invoiceNumber: json['invoice_number'] as String?,
      vendorName: json['vendor_name'] as String?,
      amount: (json['amount'] as num?)?.toDouble(),
      exceptionType: type,
      typeLabel: json['type_label'] as String? ?? type,
      severity: ApExceptionSeverity.fromString(json['severity'] as String?),
      description: json['description'] as String?,
      status: ApExceptionStatus.fromString(json['status'] as String? ?? 'open'),
      resolution: json['resolution'] as String?,
      assignedTo: json['assigned_to'] as String?,
      isOverdue: json['is_overdue'] as bool? ?? false,
      createdAt: json['created_at'] != null &&
              (json['created_at'] as String).isNotEmpty
          ? DateTime.parse(json['created_at'] as String)
          : DateTime.fromMillisecondsSinceEpoch(0),
    );
  }
}
