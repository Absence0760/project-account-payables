import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/models/payment.dart';
import 'package:ap_mobile/models/payment_queue.dart';
import 'package:ap_mobile/stores/auth_store.dart';
import 'package:ap_mobile/stores/payment_queue_store.dart';
import 'package:ap_mobile/utils/a11y.dart';
import 'package:ap_mobile/widgets/kpi_card.dart';

final _currencyFormat = NumberFormat.currency(symbol: '\$');
final _dateFormat = DateFormat('MMM d, yyyy');

/// Format a server-supplied money display string for the UI. The parse is for
/// *rendering only* — we never do arithmetic on money on the device (totals are
/// server-computed). Falls back to the raw string if it isn't numeric.
String _money(String display) {
  final n = num.tryParse(display);
  return n == null ? display : _currencyFormat.format(n);
}

/// Payment queue + runs. Tab 1 lists approved invoices; the user ticks rows,
/// picks a method per row, and creates a draft run. Tab 2 lists the runs and
/// executes / cancels drafts. A KPI summary bar (total paid / pending /
/// rebates / queue) sits above both. Gated to admin / ap_manager / cfo.
class PaymentQueueScreen extends StatefulWidget {
  const PaymentQueueScreen({super.key});

  @override
  State<PaymentQueueScreen> createState() => _PaymentQueueScreenState();
}

class _PaymentQueueScreenState extends State<PaymentQueueScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;
  bool _busy = false;

  bool get _canManage => AuthStore.instance.canManagePayments;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 2, vsync: this);
    SchedulerBinding.instance.addPostFrameCallback((_) {
      PaymentQueueStore.instance.fetch();
      PaymentQueueStore.instance.fetchRuns();
    });
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Pay'),
        bottom: TabBar(
          controller: _tabs,
          tabs: const [
            Tab(text: 'Queue'),
            Tab(text: 'Runs'),
          ],
        ),
      ),
      body: Column(
        children: [
          _summaryBar(),
          Expanded(
            child: TabBarView(
              controller: _tabs,
              children: [
                _queueTab(),
                _runsTab(),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // ── Summary KPI bar ────────────────────────────────────────────────
  Widget _summaryBar() {
    return ListenableBuilder(
      listenable: PaymentQueueStore.instance,
      builder: (context, _) {
        final s = PaymentQueueStore.instance.summary;
        if (s == null) return const SizedBox.shrink();
        return Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
          child: GridView.count(
            crossAxisCount: 2,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 8,
            crossAxisSpacing: 8,
            childAspectRatio: 2.4,
            children: [
              KpiCard(
                title: 'Total Paid',
                value: _money(s.totalPaidDisplay),
                icon: Icons.check_circle,
                color: Colors.green,
              ),
              KpiCard(
                title: 'Pending',
                value: _money(s.totalPendingDisplay),
                icon: Icons.hourglass_bottom,
                color: Colors.orange,
              ),
              KpiCard(
                title: 'In Queue',
                value: s.queueCount.toString(),
                subtitle: '${s.paymentCount} payments',
                icon: Icons.payments,
                color: Colors.blue,
              ),
              KpiCard(
                title: 'Card Rebates',
                value: _money(s.totalRebatesDisplay),
                icon: Icons.savings,
                color: Colors.purple,
              ),
            ],
          ),
        );
      },
    );
  }

  // ── Queue tab ──────────────────────────────────────────────────────
  Widget _queueTab() {
    return ListenableBuilder(
      listenable: PaymentQueueStore.instance,
      builder: (context, _) {
        final store = PaymentQueueStore.instance;

        if (store.loading && store.queue.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }
        if (store.error != null && store.queue.isEmpty) {
          return _errorState(store.error!, store.fetch);
        }
        if (store.queue.isEmpty) {
          return const Center(child: Text('No invoices awaiting payment'));
        }

        return Column(
          children: [
            Expanded(
              child: RefreshIndicator(
                onRefresh: store.fetch,
                child: ListView.separated(
                  itemCount: store.queue.length,
                  separatorBuilder: (_, _) => const Divider(height: 1),
                  itemBuilder: (context, index) =>
                      _queueRow(store.queue[index]),
                ),
              ),
            ),
            if (_canManage && store.hasSelection) _createRunBar(store),
          ],
        );
      },
    );
  }

  Widget _queueRow(PaymentQueueItem item) {
    final store = PaymentQueueStore.instance;
    final selected = store.isSelected(item.id);
    final dueText = item.dueDate != null
        ? 'Due ${_dateFormat.format(item.dueDate!)}'
        : 'No due date';

    final subtitleParts = <String>[
      item.invoiceNumber,
      dueText,
      if (item.discountEligible && item.discountAmountDisplay != null)
        'discount ${_money(item.discountAmountDisplay!)}',
    ];

    return Semantics(
      label: '${item.vendorName}, ${_money(item.amountDisplay)}, '
          '${subtitleParts.join(', ')}'
          '${item.isOverdue ? ', overdue' : ''}'
          '${selected ? ', selected' : ''}',
      excludeSemantics: true,
      child: ListTile(
        leading: _canManage
            ? Checkbox(
                value: selected,
                onChanged: (_) => store.toggleSelection(item.id),
              )
            : null,
        title: Row(
          children: [
            Expanded(
              child: Text(
                item.vendorName,
                style: const TextStyle(fontWeight: FontWeight.w600),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            Text(
              _money(item.amountDisplay),
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
          ],
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Row(
            children: [
              Flexible(
                child: Text(
                  item.invoiceNumber,
                  style: TextStyle(color: Colors.grey.shade700, fontSize: 13),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 12),
              Text(
                dueText,
                style: TextStyle(
                  color: item.isOverdue
                      ? Colors.red.shade700
                      : Colors.grey.shade700,
                  fontSize: 12,
                ),
              ),
              const Spacer(),
              if (_canManage && selected) _methodDropdown(item),
            ],
          ),
        ),
        onTap: _canManage ? () => store.toggleSelection(item.id) : null,
      ),
    );
  }

  Widget _methodDropdown(PaymentQueueItem item) {
    final store = PaymentQueueStore.instance;
    return Semantics(
      label: 'Payment method for ${item.invoiceNumber}',
      child: DropdownButton<PaymentMethod>(
        value: store.methodFor(item.id),
        isDense: true,
        underline: const SizedBox.shrink(),
        items: PaymentMethod.values
            .map(
              (m) => DropdownMenuItem(
                value: m,
                child: Text(m.label, style: const TextStyle(fontSize: 13)),
              ),
            )
            .toList(),
        onChanged: (m) {
          if (m != null) store.setMethod(item.id, m);
        },
      ),
    );
  }

  Widget _createRunBar(PaymentQueueStore store) {
    final count = store.selectedCount;
    return Material(
      elevation: 8,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  '$count ${count == 1 ? 'invoice' : 'invoices'} selected',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
              TextButton(
                onPressed: _busy ? null : store.clearSelection,
                child: const Text('Clear'),
              ),
              const SizedBox(width: 8),
              FilledButton.icon(
                onPressed: _busy ? null : _createRun,
                icon: const Icon(Icons.playlist_add_check),
                label: const Text('Create Run'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _createRun() async {
    setState(() => _busy = true);
    final message =
        await PaymentQueueStore.instance.createRunFromSelection();
    if (!mounted) return;
    setState(() => _busy = false);
    final text =
        message ?? 'Failed to create run: ${PaymentQueueStore.instance.error}';
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
    A11y.announce(context, text);
    if (message != null) _tabs.animateTo(1); // jump to Runs
  }

  // ── Runs tab ───────────────────────────────────────────────────────
  Widget _runsTab() {
    return ListenableBuilder(
      listenable: PaymentQueueStore.instance,
      builder: (context, _) {
        final store = PaymentQueueStore.instance;
        if (store.runs.isEmpty) {
          return RefreshIndicator(
            onRefresh: store.fetchRuns,
            child: ListView(
              children: const [
                SizedBox(height: 120),
                Center(child: Text('No payment runs')),
              ],
            ),
          );
        }
        return RefreshIndicator(
          onRefresh: store.fetchRuns,
          child: ListView.separated(
            itemCount: store.runs.length,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (context, index) => _runRow(store.runs[index]),
          ),
        );
      },
    );
  }

  Widget _runRow(PaymentRun run) {
    final subtitle =
        '${run.paymentCount} ${run.paymentCount == 1 ? 'payment' : 'payments'} • '
        '${_dateFormat.format(run.createdAt)}'
        '${run.requiresCfoApproval && !run.cfoApproved ? ' • CFO approval required' : ''}';
    return Semantics(
      label: 'Run ${_money(run.totalAmountDisplay)}, ${run.status}, $subtitle',
      excludeSemantics: true,
      child: ListTile(
        title: Row(
          children: [
            Expanded(
              child: Text(
                _money(run.totalAmountDisplay),
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ),
            _runStatusChip(run.status),
          ],
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Text(
            subtitle,
            style: TextStyle(color: Colors.grey.shade700, fontSize: 12),
          ),
        ),
        trailing: _canManage && run.isExecutable
            ? Semantics(
                label: 'Run actions',
                button: true,
                child: PopupMenuButton<String>(
                  onSelected: (v) => _runMenuAction(run, v),
                  itemBuilder: (_) => const [
                    PopupMenuItem(value: 'execute', child: Text('Execute')),
                    PopupMenuItem(value: 'cancel', child: Text('Cancel')),
                  ],
                ),
              )
            : null,
      ),
    );
  }

  Future<void> _runMenuAction(PaymentRun run, String action) async {
    if (action == 'execute') {
      // CFO approval is a server-side gate; surface it before attempting.
      if (run.requiresCfoApproval && !run.cfoApproved) {
        final text =
            'This run needs CFO approval before it can be executed.';
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(text)));
        A11y.announce(context, text);
        return;
      }
      final confirmed = await _confirm(
        'Execute payment run?',
        'This sends ${_money(run.totalAmountDisplay)} via the configured '
            'payment processor.',
      );
      if (confirmed != true) return;
      final message = await PaymentQueueStore.instance.executeRun(run.id);
      if (!mounted) return;
      final text = message ??
          'Failed to execute: ${PaymentQueueStore.instance.error}';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
      A11y.announce(context, text);
    } else {
      final message = await PaymentQueueStore.instance.cancelRun(run.id);
      if (!mounted) return;
      final text =
          message ?? 'Failed to cancel: ${PaymentQueueStore.instance.error}';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
      A11y.announce(context, text);
    }
  }

  Future<bool?> _confirm(String title, String body) {
    return showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Execute'),
          ),
        ],
      ),
    );
  }

  Widget _runStatusChip(String status) {
    // Darkened foreground for AA contrast over the pale tint (≥4.5:1).
    final (color, textColor) = switch (status) {
      'draft' => (Colors.blueGrey, Colors.blueGrey.shade800),
      'completed' => (Colors.green, Colors.green.shade900),
      'submitted' => (Colors.blue, Colors.blue.shade800),
      'partial' => (Colors.orange, Colors.brown.shade800),
      'failed' => (Colors.red, Colors.red.shade900),
      'cancelled' => (Colors.grey, Colors.grey.shade800),
      _ => (Colors.grey, Colors.grey.shade800),
    };
    final label = status.isEmpty
        ? status
        : status[0].toUpperCase() + status.substring(1);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: textColor,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }

  Widget _errorState(String message, Future<void> Function() onRetry) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.error_outline, size: 48, color: Colors.red.shade700),
          const SizedBox(height: 12),
          const Text('Could not load the payment queue'),
          const SizedBox(height: 12),
          FilledButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    );
  }
}
