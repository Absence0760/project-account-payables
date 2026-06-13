import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/contract.dart';
import 'package:ap_mobile/stores/auth_store.dart';
import 'package:ap_mobile/stores/contract_store.dart';
import 'package:ap_mobile/widgets/contract_status_badge.dart';
import 'package:ap_mobile/widgets/kpi_card.dart';

final _currencyFormat = NumberFormat.currency(symbol: '\$');
final _dateFormat = DateFormat('MMM d, yyyy');

class ContractDetailScreen extends StatefulWidget {
  final String contractId;

  const ContractDetailScreen({super.key, required this.contractId});

  @override
  State<ContractDetailScreen> createState() => _ContractDetailScreenState();
}

class _ContractDetailScreenState extends State<ContractDetailScreen> {
  Contract? _contract;
  bool _loading = true;
  String? _error;
  // True while a lifecycle network call is in flight — guards against a
  // double-tap firing the transition POST twice and disables the buttons.
  bool _submitting = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final contract = await ContractApi.getById(widget.contractId);
      setState(() {
        _contract = contract;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _activate() async {
    if (_submitting) return;
    setState(() => _submitting = true);
    try {
      final success =
          await ContractStore.instance.activate(widget.contractId);
      if (!mounted) return;
      if (success) {
        await _load();
        _showSnack('Contract activated');
      } else {
        _showSnack('Could not activate contract — please try again');
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _terminate() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Terminate Contract'),
        content: const Text(
          'This ends the contract early. This cannot be undone. Continue?',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Terminate'),
          ),
        ],
      ),
    );

    if (confirmed != true || _submitting) return;

    setState(() => _submitting = true);
    try {
      final success =
          await ContractStore.instance.terminate(widget.contractId);
      if (!mounted) return;
      if (success) {
        await _load();
        _showSnack('Contract terminated');
      } else {
        _showSnack('Could not terminate contract — please try again');
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  void _showSnack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Contract Detail')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? _buildError()
              : _buildDetail(),
      bottomNavigationBar: _buildActions(),
    );
  }

  Widget _buildError() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.error_outline, size: 48, color: Colors.red.shade400),
          const SizedBox(height: 12),
          Text('Error: $_error', textAlign: TextAlign.center),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: _load,
            icon: const Icon(Icons.refresh),
            label: const Text('Retry'),
          ),
        ],
      ),
    );
  }

  Widget _buildDetail() {
    final c = _contract!;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // Header
          Row(
            children: [
              Expanded(
                child: Text(
                  c.title ?? c.vendorName ?? 'Untitled Contract',
                  style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
              ContractStatusBadge(status: c.status),
            ],
          ),
          if (c.totalValue != null) ...[
            const SizedBox(height: 8),
            Text(
              _currencyFormat.format(c.totalValue),
              style: const TextStyle(
                fontSize: 28,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],

          const SizedBox(height: 24),

          // Details
          _detailRow('Contract #', c.contractNumber),
          _detailRow('Vendor', c.vendorName),
          _detailRow('Type', c.contractType.label),
          _detailRow('Currency', c.currency),
          _detailRow(
            'Spend Limit',
            c.spendLimit != null
                ? '${_currencyFormat.format(c.spendLimit)}'
                    '${c.notToExceed ? ' (not to exceed)' : ''}'
                : null,
          ),
          _detailRow(
            'Start Date',
            c.startDate != null ? _dateFormat.format(c.startDate!) : null,
          ),
          _detailRow(
            'End Date',
            c.endDate != null ? _dateFormat.format(c.endDate!) : null,
          ),
          _detailRow(
            'Signed',
            c.signedDate != null ? _dateFormat.format(c.signedDate!) : null,
          ),
          _detailRow('Auto-Renew', c.autoRenew ? 'Yes' : 'No'),
          _detailRow(
            'Renewal Term',
            c.renewalTermMonths != null
                ? '${c.renewalTermMonths} months'
                : null,
          ),
          _detailRow(
            'Renewal Notice',
            c.renewalNoticeDays != null
                ? '${c.renewalNoticeDays} days'
                : null,
          ),
          _detailRow('Payment Terms', c.paymentTerms),
          _detailRow('Description', c.description),
          _detailRow('Created', _dateFormat.format(c.createdAt)),

          // Spend summary
          if (c.spend != null) ...[
            const SizedBox(height: 24),
            _sectionTitle('Spend'),
            const SizedBox(height: 12),
            _buildSpend(c.spend!),
          ],

          // Line items
          if (c.lineItems.isNotEmpty) ...[
            const SizedBox(height: 24),
            _sectionTitle('Line Items'),
            const SizedBox(height: 8),
            ...c.lineItems.map(_buildLineItem),
          ],
        ],
      ),
    );
  }

  Widget _buildSpend(ContractSpend spend) {
    return Column(
      children: [
        Row(
          children: [
            Expanded(
              child: KpiCard(
                title: 'Invoiced',
                value: _currencyFormat.format(spend.invoicedTotal),
                subtitle: '${spend.invoiceCount} invoice'
                    '${spend.invoiceCount == 1 ? '' : 's'}',
                icon: Icons.receipt_long,
                color: Colors.blue,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: KpiCard(
                title: spend.overLimit ? 'Over Limit' : 'Remaining',
                value: spend.remaining != null
                    ? _currencyFormat.format(spend.remaining)
                    : '—',
                subtitle: spend.spendLimit != null
                    ? 'of ${_currencyFormat.format(spend.spendLimit)}'
                    : 'no limit set',
                icon: spend.overLimit
                    ? Icons.warning_amber
                    : Icons.account_balance_wallet,
                color: spend.overLimit ? Colors.red : Colors.green,
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildLineItem(ContractLineItem item) {
    final subtitleParts = <String>[
      if (item.quantity != null) 'Qty ${item.quantity}',
      if (item.unitPrice != null)
        '@ ${_currencyFormat.format(item.unitPrice)}',
      if (item.glAccount != null) 'GL ${item.glAccount}',
    ];
    return ListTile(
      contentPadding: EdgeInsets.zero,
      dense: true,
      leading: item.lineNumber != null
          ? CircleAvatar(
              radius: 14,
              child: Text(
                '${item.lineNumber}',
                style: const TextStyle(fontSize: 12),
              ),
            )
          : null,
      title: Text(
        item.description ?? item.itemCode ?? 'Line item',
        style: const TextStyle(fontSize: 14),
      ),
      subtitle:
          subtitleParts.isNotEmpty ? Text(subtitleParts.join('  ·  ')) : null,
      trailing: item.total != null
          ? Text(
              _currencyFormat.format(item.total),
              style: const TextStyle(fontWeight: FontWeight.w600),
            )
          : null,
    );
  }

  Widget _sectionTitle(String title) {
    return Text(
      title,
      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
    );
  }

  Widget _detailRow(String label, String? value) {
    if (value == null || value.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 120,
            child: Text(
              label,
              style: TextStyle(
                color: Colors.grey.shade600,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
          Expanded(child: Text(value)),
        ],
      ),
    );
  }

  Widget? _buildActions() {
    final c = _contract;
    if (c == null) return null;
    if (!c.status.isActionable) return null;
    if (!AuthStore.instance.canApprove) return null;

    final canActivate = c.status == ContractStatus.draft;

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: _submitting ? null : _terminate,
                icon: const Icon(Icons.block, color: Colors.red),
                label: const Text(
                  'Terminate',
                  style: TextStyle(color: Colors.red),
                ),
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  side: const BorderSide(color: Colors.red),
                ),
              ),
            ),
            if (canActivate) ...[
              const SizedBox(width: 16),
              Expanded(
                child: FilledButton.icon(
                  onPressed: _submitting ? null : _activate,
                  icon: const Icon(Icons.check),
                  label: const Text('Activate'),
                  style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    backgroundColor: Colors.green,
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
