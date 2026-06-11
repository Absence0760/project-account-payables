import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/stores/dashboard_store.dart';
import 'package:ap_mobile/widgets/kpi_card.dart';

final _currencyFormat = NumberFormat.compactCurrency(symbol: '\$');

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  @override
  void initState() {
    super.initState();
    SchedulerBinding.instance.addPostFrameCallback((_) {
      DashboardStore.instance.fetch();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Dashboard')),
      body: ListenableBuilder(
        listenable: DashboardStore.instance,
        builder: (context, _) {
          final store = DashboardStore.instance;

          if (store.loading && store.data == null) {
            return const Center(child: CircularProgressIndicator());
          }

          if (store.error != null && store.data == null) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text('Error: ${store.error}'),
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: store.fetch,
                    child: const Text('Retry'),
                  ),
                ],
              ),
            );
          }

          final data = store.data;
          if (data == null) return const SizedBox.shrink();

          return RefreshIndicator(
            onRefresh: store.fetch,
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                // Offline indicator — data is being served from the local
                // cache because the last refresh couldn't reach the server.
                if (store.fromCache) ...[
                  _cacheBanner(),
                  const SizedBox(height: 12),
                ],

                // KPI row
                Row(
                  children: [
                    Expanded(
                      child: KpiCard(
                        title: 'Total Invoices',
                        value: data.totalInvoices.toString(),
                        subtitle: _currencyFormat.format(data.totalAmount),
                        icon: Icons.receipt_long,
                        color: Colors.blue,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: KpiCard(
                        title: 'Upcoming',
                        value: data.upcoming.count.toString(),
                        subtitle: _currencyFormat
                            .format(data.upcoming.totalAmount),
                        icon: Icons.schedule,
                        color: Colors.orange,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: KpiCard(
                        title: 'For Review',
                        value: (data.pipeline['ready_for_review'] ?? 0)
                            .toString(),
                        icon: Icons.rate_review,
                        color: Colors.amber.shade700,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: KpiCard(
                        title: 'Approved',
                        value: (data.pipeline['approved'] ?? 0).toString(),
                        icon: Icons.check_circle,
                        color: Colors.green,
                      ),
                    ),
                  ],
                ),

                // Aging
                const SizedBox(height: 24),
                Text(
                  'Invoice Aging',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                Card(
                  elevation: 0,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                    side: BorderSide(color: Colors.grey.shade200),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Row(
                      children: [
                        _agingBucket(
                          'Current',
                          data.aging.current,
                          Colors.green,
                        ),
                        _agingBucket(
                          '30 Days',
                          data.aging.thirtyDays,
                          Colors.amber,
                        ),
                        _agingBucket(
                          '60 Days',
                          data.aging.sixtyDays,
                          Colors.orange,
                        ),
                        _agingBucket(
                          '90+',
                          data.aging.ninetyPlus,
                          Colors.red,
                        ),
                      ],
                    ),
                  ),
                ),

                // Top vendors
                const SizedBox(height: 24),
                Text(
                  'Top Vendors',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                ...data.topVendors.take(5).map(
                      (v) => ListTile(
                        dense: true,
                        title: Text(v.vendorName),
                        trailing: Text(
                          _currencyFormat.format(v.totalAmount),
                          style: const TextStyle(fontWeight: FontWeight.w600),
                        ),
                        subtitle: Text('${v.invoiceCount} invoices'),
                      ),
                    ),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _cacheBanner() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: Colors.amber.shade50,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.amber.shade200),
      ),
      child: Row(
        children: [
          Icon(Icons.cloud_off, size: 18, color: Colors.amber.shade800),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              "Showing cached data — couldn't reach the server",
              style: TextStyle(color: Colors.amber.shade900, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }

  Widget _agingBucket(String label, double amount, Color color) {
    return Expanded(
      child: Column(
        children: [
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(height: 4),
          Text(
            _currencyFormat.format(amount),
            style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
          ),
          Text(
            label,
            style: TextStyle(color: Colors.grey.shade600, fontSize: 11),
          ),
        ],
      ),
    );
  }
}
