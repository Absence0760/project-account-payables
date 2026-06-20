import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'package:ap_mobile/models/vendor.dart';
import 'package:ap_mobile/stores/auth_store.dart';
import 'package:ap_mobile/stores/vendor_store.dart';
import 'package:ap_mobile/utils/a11y.dart';
import 'package:ap_mobile/widgets/vendor_list_tile.dart';

/// Vendor management — list with status filters + search, verify / reject an
/// unverified vendor, and pull vendors from the connected ERP. Mutations are
/// gated to admin / ap_manager (mirrors the backend `require_roles`); for
/// read-only roles (CFO) the actions are simply not offered.
class VendorsScreen extends StatefulWidget {
  const VendorsScreen({super.key});

  @override
  State<VendorsScreen> createState() => _VendorsScreenState();
}

class _VendorsScreenState extends State<VendorsScreen> {
  final _searchController = TextEditingController();
  bool _syncing = false;

  bool get _canManage => AuthStore.instance.canManageVendors;

  @override
  void initState() {
    super.initState();
    SchedulerBinding.instance.addPostFrameCallback((_) {
      VendorStore.instance.fetch();
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
        title: const Text('Vendors'),
        actions: [
          if (_canManage)
            Semantics(
              label: 'Sync vendors from ERP',
              button: true,
              child: IconButton(
                tooltip: 'Sync from ERP',
                icon: _syncing
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.sync),
                onPressed: _syncing ? null : _syncErp,
              ),
            ),
        ],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(56),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: SearchBar(
              controller: _searchController,
              hintText: 'Search vendors...',
              leading: const Icon(Icons.search, size: 20),
              onChanged: (q) =>
                  VendorStore.instance.setSearch(q.isEmpty ? null : q),
              elevation: WidgetStateProperty.all(0),
            ),
          ),
        ),
      ),
      body: Column(
        children: [
          SizedBox(
            height: 48,
            child: ListenableBuilder(
              listenable: VendorStore.instance,
              builder: (context, _) {
                final current = VendorStore.instance.statusFilter;
                return ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  children: [
                    _filterChip('All', null, current),
                    _filterChip('Unverified', 'unverified', current),
                    _filterChip('Active', 'active', current),
                    _filterChip('Inactive', 'inactive', current),
                    _filterChip('Rejected', 'rejected', current),
                  ],
                );
              },
            ),
          ),
          Expanded(
            child: ListenableBuilder(
              listenable: VendorStore.instance,
              builder: (context, _) {
                final store = VendorStore.instance;

                if (store.loading && store.vendors.isEmpty) {
                  return const Center(child: CircularProgressIndicator());
                }

                if (store.error != null && store.vendors.isEmpty) {
                  return _ErrorState(
                    message: store.error!,
                    onRetry: store.fetch,
                  );
                }

                if (store.vendors.isEmpty) {
                  return const Center(child: Text('No vendors found'));
                }

                return RefreshIndicator(
                  onRefresh: store.fetch,
                  child: ListView.separated(
                    itemCount: store.vendors.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      return _buildRow(store.vendors[index]);
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

  Widget _buildRow(Vendor vendor) {
    final tile = VendorListTile(
      vendor: vendor,
      onTap: _canManage && vendor.status.isUnverified
          ? () => _showActions(vendor)
          : null,
    );

    // Swipe verify/reject only for unverified vendors, and only for roles that
    // may act. Everything else is a read-only row.
    if (!_canManage || !vendor.status.isUnverified) {
      return tile;
    }

    return Dismissible(
      key: ValueKey(vendor.id),
      background: _swipeBackground(
        Colors.green.shade700,
        Icons.check,
        Alignment.centerLeft,
        'Verify',
      ),
      secondaryBackground: _swipeBackground(
        Colors.red.shade700,
        Icons.block,
        Alignment.centerRight,
        'Reject',
      ),
      confirmDismiss: (direction) async {
        final verify = direction == DismissDirection.startToEnd;
        final ok = verify
            ? await VendorStore.instance.verify(vendor.id)
            : await VendorStore.instance.reject(vendor.id);
        if (mounted) {
          A11y.announce(
            context,
            ok
                ? (verify ? 'Vendor verified' : 'Vendor rejected')
                : 'Action failed',
          );
        }
        // The list refetches on success, so consume the dismiss (return false)
        // and let the refetched list drop the row — avoids a stale gap if the
        // server rejected the action.
        return false;
      },
      child: tile,
    );
  }

  Widget _swipeBackground(
    Color color,
    IconData icon,
    Alignment alignment,
    String action,
  ) {
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

  void _showActions(Vendor vendor) {
    showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) {
        return SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              ListTile(
                title: Text(
                  vendor.name,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                subtitle: const Text('Unverified vendor'),
              ),
              const Divider(height: 1),
              ListTile(
                leading: const Icon(Icons.check, color: Colors.green),
                title: const Text('Verify'),
                subtitle: const Text('Make eligible for payment'),
                onTap: () => _runAction(sheetContext, vendor, verify: true),
              ),
              ListTile(
                leading: Icon(Icons.block, color: Colors.red.shade700),
                title: const Text('Reject'),
                subtitle: const Text('Mark as invalid / duplicate'),
                onTap: () => _runAction(sheetContext, vendor, verify: false),
              ),
            ],
          ),
        );
      },
    );
  }

  Future<void> _runAction(
    BuildContext sheetContext,
    Vendor vendor, {
    required bool verify,
  }) async {
    Navigator.of(sheetContext).pop();
    final store = VendorStore.instance;
    final ok = verify ? await store.verify(vendor.id) : await store.reject(vendor.id);
    if (!mounted) return;
    A11y.announce(
      context,
      ok ? (verify ? 'Vendor verified' : 'Vendor rejected') : 'Action failed',
    );
  }

  Future<void> _syncErp() async {
    setState(() => _syncing = true);
    final message = await VendorStore.instance.syncErp();
    if (!mounted) return;
    setState(() => _syncing = false);
    final text = message ?? 'ERP sync failed: ${VendorStore.instance.error}';
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
    A11y.announce(context, text);
  }

  Widget _filterChip(String label, String? value, String? current) {
    final selected = current == value;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: FilterChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) => VendorStore.instance.setStatusFilter(value),
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  final String message;
  final Future<void> Function() onRetry;

  const _ErrorState({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.error_outline, size: 48, color: Colors.red.shade700),
          const SizedBox(height: 12),
          const Text('Could not load vendors'),
          const SizedBox(height: 12),
          FilledButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    );
  }
}
