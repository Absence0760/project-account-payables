import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/screens/invoice_detail_screen.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/widgets/invoice_list_tile.dart';

class ApprovalsScreen extends StatefulWidget {
  const ApprovalsScreen({super.key});

  @override
  State<ApprovalsScreen> createState() => _ApprovalsScreenState();
}

class _ApprovalsScreenState extends State<ApprovalsScreen> {
  @override
  void initState() {
    super.initState();
    SchedulerBinding.instance.addPostFrameCallback((_) {
      InvoiceStore.instance.fetch();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Pending Approvals')),
      body: ListenableBuilder(
        listenable: InvoiceStore.instance,
        builder: (context, _) {
          final store = InvoiceStore.instance;
          final pending = store.pendingApproval;

          if (store.loading && store.invoices.isEmpty) {
            return const Center(child: CircularProgressIndicator());
          }

          if (pending.isEmpty) {
            return const Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.check_circle, size: 64, color: Colors.green),
                  SizedBox(height: 16),
                  Text(
                    'All caught up!',
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
                  ),
                  SizedBox(height: 4),
                  Text('No invoices waiting for approval'),
                ],
              ),
            );
          }

          return RefreshIndicator(
            onRefresh: store.fetch,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                  child: Text(
                    '${pending.length} invoice${pending.length == 1 ? '' : 's'} pending',
                    style: TextStyle(
                      color: Colors.grey.shade600,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
                Expanded(
                  child: ListView.separated(
                    itemCount: pending.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final invoice = pending[index];
                      return Dismissible(
                        key: ValueKey(invoice.id),
                        background: _swipeBackground(
                          Colors.green,
                          Icons.check,
                          Alignment.centerLeft,
                        ),
                        secondaryBackground: _swipeBackground(
                          Colors.red,
                          Icons.close,
                          Alignment.centerRight,
                        ),
                        confirmDismiss: (direction) async {
                          if (direction == DismissDirection.startToEnd) {
                            return await InvoiceStore.instance
                                .approve(invoice.id);
                          }
                          return false; // Reject needs a reason — open detail
                        },
                        child: InvoiceListTile(
                          invoice: invoice,
                          onTap: () => _openDetail(invoice),
                        ),
                      );
                    },
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _swipeBackground(Color color, IconData icon, Alignment alignment) {
    return Container(
      color: color.withValues(alpha: 0.15),
      alignment: alignment,
      padding: const EdgeInsets.symmetric(horizontal: 24),
      child: Icon(icon, color: color),
    );
  }

  void _openDetail(Invoice invoice) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => InvoiceDetailScreen(invoiceId: invoice.id),
      ),
    );
  }
}
