import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'package:ap_mobile/l10n/gen/app_localizations.dart';
import 'package:ap_mobile/models/exception.dart';
import 'package:ap_mobile/stores/exception_store.dart';
import 'package:ap_mobile/utils/a11y.dart';
import 'package:ap_mobile/widgets/exception_list_tile.dart';

/// Exception queue — list flagged invoices with status filters and act on each
/// (resolve / escalate / dismiss). Swipe a row right to resolve, left to
/// dismiss; the trailing menu carries escalate (a third action doesn't map onto
/// a binary swipe). Mirrors `ApprovalsScreen` + `InvoicesScreen`.
class ExceptionsScreen extends StatefulWidget {
  const ExceptionsScreen({super.key});

  @override
  State<ExceptionsScreen> createState() => _ExceptionsScreenState();
}

class _ExceptionsScreenState extends State<ExceptionsScreen> {
  @override
  void initState() {
    super.initState();
    SchedulerBinding.instance.addPostFrameCallback((_) {
      ExceptionStore.instance.fetch();
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(l.exceptionsTitle)),
      body: Column(
        children: [
          // Status filter chips.
          SizedBox(
            height: 48,
            child: ListenableBuilder(
              listenable: ExceptionStore.instance,
              builder: (context, _) {
                final current = ExceptionStore.instance.statusFilter;
                return ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  children: [
                    _filterChip(l.commonAll, null, current),
                    _filterChip(l.exceptionsFilterOpen, 'open', current),
                    _filterChip(l.exceptionsFilterEscalated, 'escalated', current),
                    _filterChip(l.exceptionsFilterResolved, 'resolved', current),
                    _filterChip(l.exceptionsFilterDismissed, 'dismissed', current),
                  ],
                );
              },
            ),
          ),

          // Exception list.
          Expanded(
            child: ListenableBuilder(
              listenable: ExceptionStore.instance,
              builder: (context, _) {
                final store = ExceptionStore.instance;

                if (store.loading && store.exceptions.isEmpty) {
                  return const Center(child: CircularProgressIndicator());
                }

                if (store.exceptions.isEmpty) {
                  return Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(
                          Icons.check_circle,
                          size: 64,
                          color: Colors.green,
                        ),
                        const SizedBox(height: 16),
                        Text(
                          l.exceptionsEmpty,
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(l.exceptionsQueueClear),
                      ],
                    ),
                  );
                }

                return RefreshIndicator(
                  onRefresh: store.fetch,
                  child: ListView.separated(
                    itemCount: store.exceptions.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final exc = store.exceptions[index];
                      return _buildRow(exc);
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

  Widget _buildRow(ApException exc) {
    final l = AppLocalizations.of(context);
    final tile = ExceptionListTile(
      exception: exc,
      onTap: () => _showActions(exc),
    );

    // Terminal exceptions (resolved / dismissed) are read-only — no swipe.
    if (!exc.status.isActionable) {
      return tile;
    }

    return Dismissible(
      key: ValueKey(exc.id),
      background: _swipeBackground(
        Colors.green.shade700,
        Icons.check,
        Alignment.centerLeft,
        l.exceptionActionResolve,
      ),
      secondaryBackground: _swipeBackground(
        Colors.blueGrey.shade700,
        Icons.block,
        Alignment.centerRight,
        l.exceptionActionDismiss,
      ),
      confirmDismiss: (direction) async {
        final resolve = direction == DismissDirection.startToEnd;
        final ok = resolve
            ? await ExceptionStore.instance.resolve(exc.id)
            : await ExceptionStore.instance.dismiss(exc.id);
        if (ok && mounted) {
          // The row vanishing isn't announced on its own (WCAG 4.1.3).
          A11y.announce(
            context,
            resolve ? l.exceptionResolved : l.exceptionDismissed,
          );
        }
        return ok;
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

  /// Bottom-sheet action menu — surfaces all three actions (escalate has no
  /// swipe) and stays reachable for terminal rows that show details only.
  void _showActions(ApException exc) {
    if (!exc.status.isActionable) return;
    final l = AppLocalizations.of(context);
    showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) {
        return SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              ListTile(
                leading: const Icon(Icons.check, color: Colors.green),
                title: Text(l.exceptionActionResolve),
                onTap: () => _runAction(sheetContext, exc, 'resolve'),
              ),
              ListTile(
                leading: Icon(Icons.arrow_upward, color: Colors.red.shade700),
                title: Text(l.exceptionActionEscalate),
                onTap: () => _runAction(sheetContext, exc, 'escalate'),
              ),
              ListTile(
                leading: Icon(Icons.block, color: Colors.blueGrey.shade700),
                title: Text(l.exceptionActionDismiss),
                onTap: () => _runAction(sheetContext, exc, 'dismiss'),
              ),
            ],
          ),
        );
      },
    );
  }

  Future<void> _runAction(
    BuildContext sheetContext,
    ApException exc,
    String action,
  ) async {
    Navigator.of(sheetContext).pop();
    final l = AppLocalizations.of(context);
    final store = ExceptionStore.instance;
    final ok = switch (action) {
      'resolve' => await store.resolve(exc.id),
      'escalate' => await store.escalate(exc.id),
      _ => await store.dismiss(exc.id),
    };
    if (!mounted) return;
    final successMessage = switch (action) {
      'resolve' => l.exceptionResolved,
      'escalate' => l.exceptionEscalated,
      _ => l.exceptionDismissed,
    };
    A11y.announce(
      context,
      ok ? successMessage : l.exceptionActionFailed,
    );
  }

  Widget _filterChip(String label, String? value, String? current) {
    final selected = current == value;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: FilterChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) => ExceptionStore.instance.setStatusFilter(value),
      ),
    );
  }
}
