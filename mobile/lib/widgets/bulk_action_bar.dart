import 'package:flutter/material.dart';

/// Bottom action bar shown while the invoice list is in multi-select mode.
/// Surfaces the selected-count plus the two bulk actions (status-change /
/// delete). Pure presentation — the parent owns the store, the selection and
/// the confirmations; this just renders enabled/disabled buttons and forwards
/// taps. Reused shape so any future bulk surface (e.g. vendors) can adopt it.
class BulkActionBar extends StatelessWidget {
  final int selectedCount;
  final bool busy;
  final VoidCallback? onStatusChange;
  final VoidCallback? onDelete;

  const BulkActionBar({
    super.key,
    required this.selectedCount,
    this.busy = false,
    this.onStatusChange,
    this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    final hasSelection = selectedCount > 0 && !busy;
    return Material(
      elevation: 8,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(
            children: [
              Text(
                '$selectedCount selected',
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
              const Spacer(),
              if (busy)
                const Padding(
                  padding: EdgeInsets.only(right: 12),
                  child: SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                ),
              TextButton.icon(
                onPressed: hasSelection ? onStatusChange : null,
                icon: const Icon(Icons.swap_horiz),
                label: const Text('Status'),
              ),
              const SizedBox(width: 4),
              TextButton.icon(
                onPressed: hasSelection ? onDelete : null,
                icon: Icon(Icons.delete_outline, color: Colors.red.shade700),
                label: Text(
                  'Delete',
                  // shade700 keeps the destructive label at AA contrast.
                  style: TextStyle(color: Colors.red.shade700),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
