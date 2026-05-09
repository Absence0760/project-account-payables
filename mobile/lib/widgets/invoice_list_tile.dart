import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/widgets/status_badge.dart';

final _currencyFormat = NumberFormat.currency(symbol: '\$');

class InvoiceListTile extends StatelessWidget {
  final Invoice invoice;
  final VoidCallback? onTap;

  const InvoiceListTile({
    super.key,
    required this.invoice,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      onTap: onTap,
      title: Row(
        children: [
          Expanded(
            child: Text(
              invoice.vendorName ?? 'Unknown Vendor',
              style: const TextStyle(fontWeight: FontWeight.w600),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (invoice.amount != null)
            Text(
              _currencyFormat.format(invoice.amount),
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
        ],
      ),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(
          children: [
            if (invoice.invoiceNumber != null) ...[
              Text(
                invoice.invoiceNumber!,
                style: TextStyle(
                  color: Colors.grey.shade600,
                  fontSize: 13,
                ),
              ),
              const SizedBox(width: 12),
            ],
            StatusBadge(status: invoice.status),
            const Spacer(),
            if (invoice.dueDate != null)
              Text(
                DateFormat('MMM d').format(invoice.dueDate!),
                style: TextStyle(
                  color: invoice.dueDate!.isBefore(DateTime.now())
                      ? Colors.red
                      : Colors.grey.shade500,
                  fontSize: 12,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
