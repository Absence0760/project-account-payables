import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/screens/invoice_detail_screen.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/utils/a11y.dart';
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
                      // shade700 clears AA contrast (shade600 is 4.38:1 at 14px).
                      color: Colors.grey.shade700,
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
                          Colors.green.shade700,
                          Icons.check,
                          Alignment.centerLeft,
                          'Approve',
                        ),
                        secondaryBackground: _swipeBackground(
                          Colors.red.shade700,
                          Icons.close,
                          Alignment.centerRight,
                          'Reject',
                        ),
                        confirmDismiss: (direction) async {
                          if (direction == DismissDirection.startToEnd) {
                            final ok = await InvoiceStore.instance
                                .approve(invoice.id);
                            if (ok && context.mounted) {
                              // Announce the result for assistive tech — the
                              // row vanishing is not announced on its own
                              // (WCAG 4.1.3).
                              A11y.announce(context, 'Invoice approved');
                            }
                            return ok;
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

  Widget _swipeBackground(
    Color color,
    IconData icon,
    Alignment alignment,
    String action,
  ) {
    // Label the swipe affordance so the icon isn't an unlabelled glyph
    // (WCAG 1.1.1); the action is also surfaced visually as text.
    return Container(
      color: color.withValues(alpha: 0.15),
      alignment: alignment,
      padding: const EdgeInsets.symmetric(horizontal: 24),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: color),
          const SizedBox(width: 8),
          Text(
            action,
            style: TextStyle(color: color, fontWeight: FontWeight.w600),
          ),
        ],
      ),
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
