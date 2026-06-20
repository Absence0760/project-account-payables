import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/models/invoice.dart';

final _editDateFormat = DateFormat('MMM d, yyyy');

/// The partial PATCH body the edit sheet hands back on Save. Keys match the
/// backend `InvoiceUpdate` schema (`vendor` maps to `vendor_name` server-side).
/// Only changed fields are included. Money is string-Decimal — never a float —
/// so the backend's Pydantic `Decimal` parses it without precision loss.
typedef InvoiceEditResult = Map<String, dynamic>;

/// Modal bottom-sheet form for editing an invoice's core fields. Pure UI: it
/// computes the partial diff and returns it via `Navigator.pop`; the caller
/// performs the `PATCH` and refresh. Used by [showInvoiceEditSheet].
class InvoiceEditSheet extends StatefulWidget {
  final Invoice invoice;

  const InvoiceEditSheet({super.key, required this.invoice});

  @override
  State<InvoiceEditSheet> createState() => _InvoiceEditSheetState();
}

class _InvoiceEditSheetState extends State<InvoiceEditSheet> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _vendor;
  late final TextEditingController _invoiceNumber;
  late final TextEditingController _amount;
  late final TextEditingController _poNumber;
  late final TextEditingController _glAccount;
  late final TextEditingController _description;
  DateTime? _dueDate;

  @override
  void initState() {
    super.initState();
    final inv = widget.invoice;
    _vendor = TextEditingController(text: inv.vendorName ?? '');
    _invoiceNumber = TextEditingController(text: inv.invoiceNumber ?? '');
    // Show the amount with full precision (no thousands separator) so the
    // round-trip back to a string-Decimal is exact.
    _amount = TextEditingController(
      text: inv.amount != null ? _amountText(inv.amount!) : '',
    );
    _poNumber = TextEditingController(text: inv.poNumber ?? '');
    _glAccount = TextEditingController(text: inv.glAccount ?? '');
    _description = TextEditingController(text: inv.description ?? '');
    _dueDate = inv.dueDate;
  }

  @override
  void dispose() {
    _vendor.dispose();
    _invoiceNumber.dispose();
    _amount.dispose();
    _poNumber.dispose();
    _glAccount.dispose();
    _description.dispose();
    super.dispose();
  }

  /// Render a double back to a plain decimal string (strip a trailing `.0`).
  static String _amountText(double v) {
    final s = v.toString();
    return s.endsWith('.0') ? s.substring(0, s.length - 2) : s;
  }

  String? _validateAmount(String? raw) {
    final v = (raw ?? '').trim();
    if (v.isEmpty) return null; // amount may be cleared (optional field)
    // Accept only a plain non-negative decimal — this string is sent verbatim
    // to the backend Decimal, so reject anything that isn't a clean number.
    if (!RegExp(r'^\d+(\.\d+)?$').hasMatch(v)) {
      return 'Enter a valid amount (e.g. 1234.56)';
    }
    return null;
  }

  /// Build the partial diff of changed fields only.
  InvoiceEditResult _buildChanges() {
    final inv = widget.invoice;
    final changes = <String, dynamic>{};

    final vendor = _vendor.text.trim();
    if (vendor != (inv.vendorName ?? '')) changes['vendor'] = vendor;

    final invoiceNumber = _invoiceNumber.text.trim();
    if (invoiceNumber != (inv.invoiceNumber ?? '')) {
      changes['invoice_number'] = invoiceNumber;
    }

    // Amount as string-Decimal (Decimal-safe). Compare on the normalised text
    // so re-saving an unchanged amount doesn't emit a no-op field.
    final amountText = _amount.text.trim();
    final originalAmountText =
        inv.amount != null ? _amountText(inv.amount!) : '';
    if (amountText != originalAmountText) {
      changes['amount'] = amountText.isEmpty ? null : amountText;
    }

    final poNumber = _poNumber.text.trim();
    if (poNumber != (inv.poNumber ?? '')) changes['po_number'] = poNumber;

    final glAccount = _glAccount.text.trim();
    if (glAccount != (inv.glAccount ?? '')) changes['gl_account'] = glAccount;

    final description = _description.text.trim();
    if (description != (inv.description ?? '')) {
      changes['description'] = description;
    }

    final originalDue = inv.dueDate;
    if (_dueDate?.toIso8601String() != originalDue?.toIso8601String()) {
      // Backend wants a YYYY-MM-DD date string.
      changes['due_date'] =
          _dueDate == null ? null : DateFormat('yyyy-MM-dd').format(_dueDate!);
    }

    return changes;
  }

  void _save() {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    Navigator.of(context).pop(_buildChanges());
  }

  Future<void> _pickDueDate() async {
    final initial = _dueDate ?? DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: DateTime(2000),
      lastDate: DateTime(2100),
    );
    if (picked != null) setState(() => _dueDate = picked);
  }

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      // Lift the sheet above the keyboard.
      padding: EdgeInsets.only(bottom: bottomInset),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
          child: Form(
            key: _formKey,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    const Expanded(
                      child: Text(
                        'Edit Invoice',
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                    Semantics(
                      label: 'Close edit form',
                      button: true,
                      child: IconButton(
                        tooltip: 'Close',
                        icon: const Icon(Icons.close),
                        onPressed: () => Navigator.of(context).pop(),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                _field(_vendor, 'Vendor', textInputAction: TextInputAction.next),
                _field(_invoiceNumber, 'Invoice #'),
                _field(
                  _amount,
                  'Amount',
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  inputFormatters: [
                    FilteringTextInputFormatter.allow(RegExp(r'[\d.]')),
                  ],
                  validator: _validateAmount,
                ),
                _field(_poNumber, 'PO Number'),
                _field(_glAccount, 'GL Account'),
                _field(_description, 'Description', maxLines: 3),
                const SizedBox(height: 8),
                _dueDateField(),
                const SizedBox(height: 20),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () => Navigator.of(context).pop(),
                        style: OutlinedButton.styleFrom(
                          padding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                        child: const Text('Cancel'),
                      ),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: FilledButton(
                        onPressed: _save,
                        style: FilledButton.styleFrom(
                          padding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                        child: const Text('Save'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _field(
    TextEditingController controller,
    String label, {
    TextInputType? keyboardType,
    List<TextInputFormatter>? inputFormatters,
    String? Function(String?)? validator,
    int maxLines = 1,
    TextInputAction? textInputAction,
  }) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: TextFormField(
        controller: controller,
        keyboardType: keyboardType,
        inputFormatters: inputFormatters,
        validator: validator,
        maxLines: maxLines,
        textInputAction: textInputAction,
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
        ),
      ),
    );
  }

  Widget _dueDateField() {
    final label =
        _dueDate != null ? _editDateFormat.format(_dueDate!) : 'Not set';
    return Semantics(
      label: 'Due date, currently $label. Double tap to change.',
      button: true,
      child: InkWell(
        onTap: _pickDueDate,
        child: InputDecorator(
          decoration: const InputDecoration(
            labelText: 'Due Date',
            border: OutlineInputBorder(),
          ),
          child: Row(
            children: [
              Expanded(child: Text(label)),
              if (_dueDate != null)
                Semantics(
                  label: 'Clear due date',
                  button: true,
                  child: IconButton(
                    tooltip: 'Clear',
                    icon: const Icon(Icons.clear, size: 18),
                    onPressed: () => setState(() => _dueDate = null),
                  ),
                ),
              const Icon(Icons.calendar_today, size: 18),
            ],
          ),
        ),
      ),
    );
  }
}

/// Open the edit sheet; resolves to the partial-changes map on Save, or null if
/// the user cancels / dismisses. An empty (non-null) map means "no fields
/// changed" — the caller can skip the PATCH.
Future<InvoiceEditResult?> showInvoiceEditSheet(
  BuildContext context,
  Invoice invoice,
) {
  return showModalBottomSheet<InvoiceEditResult>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => InvoiceEditSheet(invoice: invoice),
  );
}
