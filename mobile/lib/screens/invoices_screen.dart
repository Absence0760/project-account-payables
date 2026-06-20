import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/screens/capture_screen.dart';
import 'package:ap_mobile/screens/invoice_detail_screen.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/widgets/advanced_search_sheet.dart';
import 'package:ap_mobile/widgets/invoice_list_tile.dart';

class InvoicesScreen extends StatefulWidget {
  const InvoicesScreen({super.key});

  @override
  State<InvoicesScreen> createState() => _InvoicesScreenState();
}

class _InvoicesScreenState extends State<InvoicesScreen> {
  final _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    SchedulerBinding.instance.addPostFrameCallback((_) {
      InvoiceStore.instance.fetch();
    });
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Invoices'),
        actions: [
          // Advanced search (vendor / PO / amount range / due-date range).
          // A dot badge marks an active advanced filter so it's never invisible.
          ListenableBuilder(
            listenable: InvoiceStore.instance,
            builder: (context, _) {
              final active = !InvoiceStore.instance.filters.isEmpty;
              return Semantics(
                label: active
                    ? 'Advanced search, filters active'
                    : 'Advanced search',
                button: true,
                child: IconButton(
                  tooltip: 'Advanced Search',
                  icon: Badge(
                    isLabelVisible: active,
                    child: const Icon(Icons.tune),
                  ),
                  onPressed: _openAdvancedSearch,
                ),
              );
            },
          ),
          // Explicit label is the screen-reader name (tooltip alone isn't
          // exposed as a semantics label on all platforms — WCAG 4.1.2).
          Semantics(
            label: 'Capture invoice',
            button: true,
            child: IconButton(
              icon: const Icon(Icons.camera_alt),
              tooltip: 'Capture Invoice',
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const CaptureScreen()),
              ),
            ),
          ),
        ],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(56),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: SearchBar(
              controller: _searchController,
              hintText: 'Search invoices...',
              leading: const Icon(Icons.search, size: 20),
              onChanged: (q) => InvoiceStore.instance.setSearch(
                q.isEmpty ? null : q,
              ),
              elevation: WidgetStateProperty.all(0),
            ),
          ),
        ),
      ),
      body: Column(
        children: [
          // Status filter chips
          SizedBox(
            height: 48,
            child: ListenableBuilder(
              listenable: InvoiceStore.instance,
              builder: (context, _) {
                final current = InvoiceStore.instance.statusFilter;
                return ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  children: [
                    _filterChip('All', null, current),
                    _filterChip('New', 'new', current),
                    _filterChip('Pending', 'pending', current),
                    _filterChip('Review', 'ready_for_review', current),
                    _filterChip('Approved', 'approved', current),
                    _filterChip('Rejected', 'rejected', current),
                    _filterChip('Paid', 'paid', current),
                  ],
                );
              },
            ),
          ),

          // Invoice list
          Expanded(
            child: ListenableBuilder(
              listenable: InvoiceStore.instance,
              builder: (context, _) {
                final store = InvoiceStore.instance;

                if (store.loading && store.invoices.isEmpty) {
                  return const Center(child: CircularProgressIndicator());
                }

                if (store.invoices.isEmpty) {
                  return const Center(child: Text('No invoices found'));
                }

                return RefreshIndicator(
                  onRefresh: store.fetch,
                  child: ListView.separated(
                    itemCount: store.invoices.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final invoice = store.invoices[index];
                      return InvoiceListTile(
                        invoice: invoice,
                        onTap: () => _openDetail(invoice),
                      );
                    },
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _filterChip(String label, String? value, String? current) {
    final selected = current == value;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: FilterChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) => InvoiceStore.instance.setStatusFilter(value),
      ),
    );
  }

  Future<void> _openAdvancedSearch() async {
    final result = await showAdvancedSearchSheet(
      context,
      InvoiceStore.instance.filters,
    );
    // null = dismissed (no change); empty = Clear; otherwise Apply.
    if (result != null) {
      InvoiceStore.instance.setFilters(result);
    }
  }

  void _openDetail(Invoice invoice) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => InvoiceDetailScreen(invoiceId: invoice.id),
      ),
    );
  }
}
