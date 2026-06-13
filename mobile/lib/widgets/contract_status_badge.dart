import 'package:flutter/material.dart';

import 'package:ap_mobile/models/contract.dart';

class ContractStatusBadge extends StatelessWidget {
  final ContractStatus status;

  const ContractStatusBadge({super.key, required this.status});

  Color get _color => switch (status) {
    ContractStatus.draft => Colors.blueGrey,
    ContractStatus.active => Colors.green,
    ContractStatus.expired => Colors.orange,
    ContractStatus.terminated => Colors.red,
    ContractStatus.cancelled => Colors.grey,
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
