import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/config.dart';
import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/stores/auth_store.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/widgets/status_badge.dart';

final _currencyFormat = NumberFormat.currency(symbol: '\$');
final _dateFormat = DateFormat('MMM d, yyyy');

class InvoiceDetailScreen extends StatefulWidget {
  final String invoiceId;

  const InvoiceDetailScreen({super.key, required this.invoiceId});

  @override
  State<InvoiceDetailScreen> createState() => _InvoiceDetailScreenState();
}

class _InvoiceDetailScreenState extends State<InvoiceDetailScreen> {
  Invoice? _invoice;
  bool _loading = true;
  String? _error;
  // True while an approve/reject network call is in flight — guards against a
  // double-tap firing the money-path POST twice and disables the buttons.
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
      final invoice = await InvoiceApi.getById(widget.invoiceId);
      setState(() {
        _invoice = invoice;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _approve() async {
    if (_submitting) return;
    setState(() => _submitting = true);
    try {
      final success = await InvoiceStore.instance.approve(widget.invoiceId);
      if (!mounted) return;
      if (success) {
        await _load();
        _showSnack('Invoice approved');
      } else {
        _showSnack('Could not approve invoice — please try again');
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _reject() async {
    final reason = await showDialog<String>(
      context: context,
      builder: (context) {
        final controller = TextEditingController();
        return AlertDialog(
          title: const Text('Reject Invoice'),
          content: TextField(
            controller: controller,
            decoration: const InputDecoration(
              labelText: 'Reason',
              border: OutlineInputBorder(),
            ),
            maxLines: 3,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, controller.text),
              child: const Text('Reject'),
            ),
          ],
        );
      },
    );

    if (reason == null || reason.isEmpty || _submitting) return;

    setState(() => _submitting = true);
    try {
      final success =
          await InvoiceStore.instance.reject(widget.invoiceId, reason);
      if (!mounted) return;
      if (success) {
        await _load();
        _showSnack('Invoice rejected');
      } else {
        _showSnack('Could not reject invoice — please try again');
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
      appBar: AppBar(title: const Text('Invoice Detail')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text('Error: $_error'))
              : _buildDetail(),
      bottomNavigationBar: _buildActions(),
    );
  }

  Widget _buildDetail() {
    final inv = _invoice!;
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
                  inv.vendorName ?? 'Unknown Vendor',
                  style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
              StatusBadge(status: inv.status),
            ],
          ),
          if (inv.amount != null) ...[
            const SizedBox(height: 8),
            Text(
              _currencyFormat.format(inv.amount),
              style: const TextStyle(
                fontSize: 28,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],

          // Invoice image
          if (inv.fileUrl != null && inv.fileUrl!.isNotEmpty) ...[
            const SizedBox(height: 16),
            GestureDetector(
              onTap: () => _showFullImage(context, inv.fileUrl!),
              child: Container(
                constraints: const BoxConstraints(maxHeight: 200),
                width: double.infinity,
                decoration: BoxDecoration(
                  border: Border.all(color: Colors.grey.shade300),
                  borderRadius: BorderRadius.circular(8),
                ),
                clipBehavior: Clip.antiAlias,
                child: Image.network(
                  '${AppConfig.apiBaseUrl}${inv.fileUrl}',
                  headers: ApiClient().authHeaders,
                  fit: BoxFit.contain,
                  errorBuilder: (_, _, _) => Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.description,
                            size: 40, color: Colors.grey.shade400),
                        const SizedBox(height: 8),
                        Text(
                          'Tap to view file',
                          style: TextStyle(color: Colors.grey.shade500),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ],

          const SizedBox(height: 24),

          // Details
          _detailRow('Invoice #', inv.invoiceNumber),
          _detailRow('PO Number', inv.poNumber),
          _detailRow('Currency', inv.currency),
          _detailRow(
            'Invoice Date',
            inv.invoiceDate != null
                ? _dateFormat.format(inv.invoiceDate!)
                : null,
          ),
          _detailRow(
            'Due Date',
            inv.dueDate != null ? _dateFormat.format(inv.dueDate!) : null,
          ),
          _detailRow('Description', inv.description),
          _detailRow(
            'Created',
            _dateFormat.format(inv.createdAt),
          ),
        ],
      ),
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

  void _showFullImage(BuildContext context, String fileUrl) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => Scaffold(
          appBar: AppBar(
            backgroundColor: Colors.black,
            foregroundColor: Colors.white,
            title: const Text('Invoice Image'),
          ),
          backgroundColor: Colors.black,
          body: InteractiveViewer(
            child: Center(
              child: Image.network(
                '${AppConfig.apiBaseUrl}$fileUrl',
                headers: ApiClient().authHeaders,
                fit: BoxFit.contain,
                errorBuilder: (_, _, _) => const Center(
                  child: Text(
                    'Unable to load image',
                    style: TextStyle(color: Colors.white),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget? _buildActions() {
    final inv = _invoice;
    if (inv == null) return null;
    if (!inv.status.isActionable) return null;
    if (!AuthStore.instance.canApprove) return null;

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: _submitting ? null : _reject,
                icon: const Icon(Icons.close, color: Colors.red),
                label: const Text(
                  'Reject',
                  style: TextStyle(color: Colors.red),
                ),
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  side: const BorderSide(color: Colors.red),
                ),
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: FilledButton.icon(
                onPressed: _submitting ? null : _approve,
                icon: const Icon(Icons.check),
                label: const Text('Approve'),
                style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  backgroundColor: Colors.green,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
