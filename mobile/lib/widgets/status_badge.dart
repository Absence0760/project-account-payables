import 'package:flutter/material.dart';

import 'package:ap_mobile/models/invoice.dart';

class StatusBadge extends StatelessWidget {
  final InvoiceStatus status;

  const StatusBadge({super.key, required this.status});

  Color get _color => switch (status) {
    InvoiceStatus.newStatus => Colors.blue,
    InvoiceStatus.pending => Colors.orange,
    InvoiceStatus.readyForReview => Colors.amber,
    InvoiceStatus.approved => Colors.green,
    InvoiceStatus.rejected => Colors.red,
    InvoiceStatus.sendingToErp ||
    InvoiceStatus.sentToErp ||
    InvoiceStatus.postedInErp => Colors.indigo,
    InvoiceStatus.paymentScheduled => Colors.teal,
    InvoiceStatus.paid || InvoiceStatus.done => Colors.green.shade800,
    InvoiceStatus.failed => Colors.red.shade800,
  };

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: _color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: _color.withValues(alpha: 0.4)),
      ),
      child: Text(
        status.label,
        style: TextStyle(
          color: _color,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
