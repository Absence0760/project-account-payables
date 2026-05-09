enum InvoiceStatus {
  newStatus('new'),
  pending('pending'),
  readyForReview('ready_for_review'),
  approved('approved'),
  rejected('rejected'),
  sendingToErp('sending_to_erp'),
  sentToErp('sent_to_erp'),
  postedInErp('posted_in_erp'),
  paymentScheduled('payment_scheduled'),
  paid('paid'),
  done('done'),
  failed('failed');

  const InvoiceStatus(this.value);
  final String value;

  static InvoiceStatus fromString(String s) {
    return InvoiceStatus.values.firstWhere(
      (e) => e.value == s,
      orElse: () => InvoiceStatus.newStatus,
    );
  }

  String get label => switch (this) {
    InvoiceStatus.newStatus => 'New',
    InvoiceStatus.pending => 'Pending',
    InvoiceStatus.readyForReview => 'Ready for Review',
    InvoiceStatus.approved => 'Approved',
    InvoiceStatus.rejected => 'Rejected',
    InvoiceStatus.sendingToErp => 'Sending to ERP',
    InvoiceStatus.sentToErp => 'Sent to ERP',
    InvoiceStatus.postedInErp => 'Posted in ERP',
    InvoiceStatus.paymentScheduled => 'Payment Scheduled',
    InvoiceStatus.paid => 'Paid',
    InvoiceStatus.done => 'Done',
    InvoiceStatus.failed => 'Failed',
  };

  bool get isActionable =>
      this == InvoiceStatus.readyForReview;
}

class Invoice {
  final String id;
  final String? invoiceNumber;
  final String? vendorName;
  final double? amount;
  final String? currency;
  final InvoiceStatus status;
  final DateTime? invoiceDate;
  final DateTime? dueDate;
  final String? description;
  final String? poNumber;
  final String? fileUrl;
  final DateTime createdAt;

  Invoice({
    required this.id,
    this.invoiceNumber,
    this.vendorName,
    this.amount,
    this.currency,
    required this.status,
    this.invoiceDate,
    this.dueDate,
    this.description,
    this.poNumber,
    this.fileUrl,
    required this.createdAt,
  });

  factory Invoice.fromJson(Map<String, dynamic> json) {
    return Invoice(
      id: json['id'] as String,
      invoiceNumber: json['invoice_number'] as String?,
      vendorName: (json['vendor_name'] ?? json['vendor']) as String?,
      amount: (json['amount'] as num?)?.toDouble(),
      currency: json['currency'] as String? ?? 'USD',
      status: InvoiceStatus.fromString(json['status'] as String),
      invoiceDate: json['invoice_date'] != null
          ? DateTime.parse(json['invoice_date'] as String)
          : null,
      dueDate: json['due_date'] != null
          ? DateTime.parse(json['due_date'] as String)
          : null,
      description: json['description'] as String?,
      poNumber: json['po_number'] as String?,
      fileUrl: json['file_url'] as String?,
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }
}
