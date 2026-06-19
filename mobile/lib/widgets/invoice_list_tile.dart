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

  // A single, sensible screen-reader announcement for the whole row, instead
  // of letting the reader walk 4-5 disjoint Text spans (WCAG 1.3.1 / 4.1.2).
  String get _semanticLabel {
    final parts = <String>[
      invoice.vendorName ?? 'Unknown Vendor',
      if (invoice.amount != null) _currencyFormat.format(invoice.amount),
      if (invoice.invoiceNumber != null) 'invoice ${invoice.invoiceNumber}',
      invoice.status.label,
      if (invoice.dueDate != null)
        '${invoice.dueDate!.isBefore(DateTime.now()) ? 'past due' : 'due'} '
            '${DateFormat('MMMM d').format(invoice.dueDate!)}',
    ];
    return parts.join(', ');
  }

  @override
  Widget build(BuildContext context) {
    // Merge the inner spans into one announcement; the row stays a single
    // focusable button for assistive tech when it's tappable.
    return Semantics(
      label: _semanticLabel,
      button: onTap != null,
      excludeSemantics: true,
      child: _buildTile(),
    );
  }

  Widget _buildTile() {
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
                  // Darkened so the due-date label clears AA contrast at 12px
                  // against white (plain Colors.red / grey.shade500 fail).
                  color: invoice.dueDate!.isBefore(DateTime.now())
                      ? Colors.red.shade700
                      : Colors.grey.shade700,
                  fontSize: 12,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
