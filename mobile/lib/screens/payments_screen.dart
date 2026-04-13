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
                            return ListTile(
                              leading: _methodIcon(p.method),
                              title: Text(
                                _currencyFormat.format(p.amount),
                                style: const TextStyle(
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              subtitle: Text(
                                '${p.method.label} • ${p.reference ?? p.id.substring(0, 8)}',
                              ),
                              trailing: _statusChip(p.status),
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

  Widget _statusChip(PaymentStatus status) {
    final (label, color) = switch (status) {
      PaymentStatus.pending => ('Pending', Colors.orange),
      PaymentStatus.processing => ('Processing', Colors.blue),
      PaymentStatus.completed => ('Completed', Colors.green),
      PaymentStatus.failed => ('Failed', Colors.red),
      PaymentStatus.cancelled => ('Cancelled', Colors.grey),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        label,
        style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600),
      ),
    );
  }
}
