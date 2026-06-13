import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/models/contract.dart';
import 'package:ap_mobile/widgets/contract_status_badge.dart';

final _currencyFormat = NumberFormat.currency(symbol: '\$');

class ContractListTile extends StatelessWidget {
  final Contract contract;
  final VoidCallback? onTap;

  const ContractListTile({
    super.key,
    required this.contract,
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
              contract.title ?? contract.vendorName ?? 'Untitled Contract',
              style: const TextStyle(fontWeight: FontWeight.w600),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (contract.totalValue != null)
            Text(
              _currencyFormat.format(contract.totalValue),
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
        ],
      ),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(
          children: [
            if (contract.contractNumber != null) ...[
              Text(
                contract.contractNumber!,
                style: TextStyle(
                  color: Colors.grey.shade600,
                  fontSize: 13,
                ),
              ),
              const SizedBox(width: 12),
            ] else if (contract.vendorName != null) ...[
              Flexible(
                child: Text(
                  contract.vendorName!,
                  style: TextStyle(
                    color: Colors.grey.shade600,
                    fontSize: 13,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 12),
            ],
            ContractStatusBadge(status: contract.status),
            const Spacer(),
            if (contract.endDate != null)
              Text(
                DateFormat('MMM d, yyyy').format(contract.endDate!),
                style: TextStyle(
                  color: contract.endDate!.isBefore(DateTime.now())
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
