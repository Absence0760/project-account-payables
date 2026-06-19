import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/payment.dart';

final _currencyFormat = NumberFormat.currency(symbol: '\$');

class PaymentsScreen extends StatefulWidget {
  const PaymentsScreen({super.key});

  @override
  State<PaymentsScreen> createState() => _PaymentsScreenState();
}

class _PaymentsScreenState extends State<PaymentsScreen> {
  List<Payment> _payments = [];
  bool _loading = true;
  String? _error;

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
      _payments = await PaymentApi.list();
      setState(() => _loading = false);
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Payments')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text('Error: $_error'))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: _payments.isEmpty
                      ? const Center(child: Text('No payments'))
                      : ListView.separated(
                          itemCount: _payments.length,
                          separatorBuilder: (_, _) =>
                              const Divider(height: 1),
                          itemBuilder: (context, index) {
                            final p = _payments[index];
                            final reference =
                                p.reference ?? p.id.substring(0, 8);
                            // One merged announcement for the whole payment row.
                            return Semantics(
                              label:
                                  '${_currencyFormat.format(p.amount)}, '
                                  '${p.method.label}, $reference, '
                                  '${_statusLabel(p.status)}',
                              excludeSemantics: true,
                              child: ListTile(
                                leading: _methodIcon(p.method),
                                title: Text(
                                  _currencyFormat.format(p.amount),
                                  style: const TextStyle(
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                                subtitle: Text(
                                  '${p.method.label} • $reference',
                                ),
                                trailing: _statusChip(p.status),
                              ),
                            );
                          },
                        ),
                ),
    );
  }

  Widget _methodIcon(PaymentMethod method) {
    final (icon, color) = switch (method) {
      PaymentMethod.ach => (Icons.account_balance, Colors.blue),
      PaymentMethod.wire => (Icons.bolt, Colors.orange),
      PaymentMethod.check => (Icons.description, Colors.grey),
      PaymentMethod.virtualCard => (Icons.credit_card, Colors.purple),
    };
    return CircleAvatar(
      backgroundColor: color.withValues(alpha: 0.15),
      child: Icon(icon, color: color, size: 20),
    );
  }

  String _statusLabel(PaymentStatus status) => switch (status) {
        PaymentStatus.pending => 'Pending',
        PaymentStatus.processing => 'Processing',
        PaymentStatus.completed => 'Completed',
        PaymentStatus.failed => 'Failed',
        PaymentStatus.cancelled => 'Cancelled',
      };

  Widget _statusChip(PaymentStatus status) {
    // Tint drives the background/border; text uses a darkened variant so the
    // 12px bold label clears AA contrast (≥4.5:1) over the pale tint
    // (WCAG 1.4.3). Orange can't reach 4.5:1 as a true orange, so `pending`
    // uses a dark brown that reads as deep amber.
    final (color, textColor) = switch (status) {
      PaymentStatus.pending => (Colors.orange, Colors.brown.shade800),
      PaymentStatus.processing => (Colors.blue, Colors.blue.shade800),
      PaymentStatus.completed => (Colors.green, Colors.green.shade900),
      PaymentStatus.failed => (Colors.red, Colors.red.shade900),
      PaymentStatus.cancelled => (Colors.grey, Colors.grey.shade800),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        _statusLabel(status),
        style: TextStyle(
          color: textColor,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
