import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/audit_entry.dart';
import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/stores/auth_store.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/utils/a11y.dart';
import 'package:ap_mobile/widgets/activity_timeline.dart';
import 'package:ap_mobile/widgets/invoice_edit_sheet.dart';
import 'package:ap_mobile/widgets/invoice_file_viewer.dart';
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

  // Activity timeline (audit log) state — loaded independently of the invoice
  // body so a slow / failed trail never blocks the detail view.
  List<AuditEntry> _activity = [];
  bool _activityLoading = true;
  String? _activityError;

  @override
  void initState() {
    super.initState();
    _load();
    _loadActivity();
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

  Future<void> _loadActivity() async {
    setState(() {
      _activityLoading = true;
      _activityError = null;
    });
    try {
      final entries =
          await InvoiceStore.instance.fetchAuditLog(widget.invoiceId);
      if (!mounted) return;
      setState(() {
        _activity = entries;
        _activityLoading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _activityError = e.toString();
        _activityLoading = false;
      });
    }
  }

  Future<void> _edit() async {
    final inv = _invoice;
    if (inv == null || _submitting) return;
    final changes = await showInvoiceEditSheet(context, inv);
    if (changes == null || !mounted) return; // cancelled / dismissed
    if (changes.isEmpty) {
      _showSnack('No changes to save');
      return;
    }

    setState(() => _submitting = true);
    try {
      final updated = await InvoiceStore.instance.update(widget.invoiceId, changes);
      if (!mounted) return;
      if (updated != null) {
        // Reflect the edit + the new `invoice.edited` audit row.
        await _load();
        await _loadActivity();
        _showSnack('Invoice updated');
      } else {
        _showSnack('Could not save changes — please try again');
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
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
    // Mirror the toast to assistive tech — a SnackBar is not reliably
    // announced on its own (WCAG 4.1.3).
    A11y.announce(context, message);
  }

  bool get _canEdit {
    final inv = _invoice;
    return inv != null &&
        inv.status.isEditable &&
        AuthStore.instance.canEditInvoice;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Invoice Detail'),
        actions: [
          if (_canEdit)
            Semantics(
              label: 'Edit invoice',
              button: true,
              child: IconButton(
                tooltip: 'Edit',
                icon: const Icon(Icons.edit),
                onPressed: _submitting ? null : _edit,
              ),
            ),
        ],
      ),
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

  Future<void> _refreshAll() async {
    await _load();
    await _loadActivity();
  }

  Widget _buildDetail() {
    final inv = _invoice!;
    return RefreshIndicator(
      onRefresh: _refreshAll,
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

          // Invoice file preview (image thumbnail or PDF card → full viewer)
          if (inv.fileUrl != null && inv.fileUrl!.isNotEmpty) ...[
            const SizedBox(height: 16),
            _buildFilePreview(inv.fileUrl!),
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
          _detailRow('GL Account', inv.glAccount),
          _detailRow(
            'Created',
            _dateFormat.format(inv.createdAt),
          ),

          const SizedBox(height: 24),
          const Divider(),
          const SizedBox(height: 8),
          const Text(
            'Activity',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 8),
          _buildActivity(),
        ],
      ),
    );
  }

  Widget _buildActivity() {
    if (_activityLoading) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 16),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (_activityError != null) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Expanded(
              child: Text(
                'Could not load activity',
                style: TextStyle(color: Colors.grey.shade700),
              ),
            ),
            TextButton.icon(
              onPressed: _loadActivity,
              icon: const Icon(Icons.refresh, size: 18),
              label: const Text('Retry'),
            ),
          ],
        ),
      );
    }
    return ActivityTimeline(entries: _activity);
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

  /// Inline preview tile — an image thumbnail for image files, a PDF card for
  /// PDFs (which can't render via [Image.network]). Tapping either opens the
  /// full [InvoiceFileViewer].
  Widget _buildFilePreview(String fileUrl) {
    final isPdf = InvoiceFileViewer.isPdf(fileUrl);
    return Semantics(
      label: isPdf
          ? 'Invoice PDF. Double tap to view full screen.'
          : 'Invoice file. Double tap to view full screen.',
      button: true,
      child: GestureDetector(
        onTap: () => _openViewer(context, fileUrl),
        child: Container(
          constraints: const BoxConstraints(maxHeight: 200),
          width: double.infinity,
          decoration: BoxDecoration(
            border: Border.all(color: Colors.grey.shade300),
            borderRadius: BorderRadius.circular(8),
          ),
          clipBehavior: Clip.antiAlias,
          child: isPdf
              ? Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.picture_as_pdf,
                          size: 40, color: Colors.redAccent),
                      const SizedBox(height: 8),
                      Text(
                        'Tap to view PDF',
                        style: TextStyle(color: Colors.grey.shade700),
                      ),
                    ],
                  ),
                )
              : Image.network(
                  InvoiceFileViewer.absoluteUrl(fileUrl),
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
                          style: TextStyle(color: Colors.grey.shade700),
                        ),
                      ],
                    ),
                  ),
                ),
        ),
      ),
    );
  }

  void _openViewer(BuildContext context, String fileUrl) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => InvoiceFileViewer(fileUrl: fileUrl),
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
                icon: Icon(Icons.close, color: Colors.red.shade700),
                label: Text(
                  'Reject',
                  // shade700 keeps the destructive label at AA contrast.
                  style: TextStyle(color: Colors.red.shade700),
                ),
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  side: BorderSide(color: Colors.red.shade700),
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
