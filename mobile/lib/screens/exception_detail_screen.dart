import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/admin_user.dart';
import 'package:ap_mobile/models/exception.dart';
import 'package:ap_mobile/stores/auth_store.dart';
import 'package:ap_mobile/stores/exception_store.dart';
import 'package:ap_mobile/utils/a11y.dart';
import 'package:ap_mobile/widgets/exception_status_badge.dart';

final _currencyFormat = NumberFormat.currency(symbol: '\$');
final _dateFormat = DateFormat('MMM d, yyyy · h:mm a');

/// Single-exception detail — full fields, the linked invoice, SLA / due /
/// overdue, the current assignee, and the three actions (resolve / escalate /
/// dismiss) reachable in one place. Tapping a queue row opens this.
///
/// Loads via `GET /api/exceptions/{id}` so the freshest server state (assignee,
/// resolution, SLA) is shown even when the list row is stale/cached.
class ExceptionDetailScreen extends StatefulWidget {
  final String exceptionId;

  const ExceptionDetailScreen({super.key, required this.exceptionId});

  @override
  State<ExceptionDetailScreen> createState() => _ExceptionDetailScreenState();
}

class _ExceptionDetailScreenState extends State<ExceptionDetailScreen> {
  ApException? _exception;
  bool _loading = true;
  String? _error;
  // True while an action / assign network call is in flight — disables the
  // controls and guards against a double-tap firing twice.
  bool _submitting = false;

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
    final exc = await ExceptionStore.instance.getById(widget.exceptionId);
    if (!mounted) return;
    setState(() {
      _exception = exc;
      _error = exc == null
          ? (ExceptionStore.instance.error ?? 'Exception not found')
          : null;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Exception')),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null || _exception == null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, size: 56, color: Colors.red),
            const SizedBox(height: 12),
            Text(_error ?? 'Exception not found'),
            const SizedBox(height: 12),
            FilledButton(onPressed: _load, child: const Text('Retry')),
          ],
        ),
      );
    }

    final exc = _exception!;
    final canAct = exc.status.isActionable && !_submitting;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _header(exc),
          const SizedBox(height: 16),
          if (exc.description != null && exc.description!.isNotEmpty) ...[
            _section('Description', exc.description!),
            const SizedBox(height: 16),
          ],
          _invoicePanel(exc),
          const SizedBox(height: 16),
          _slaPanel(exc),
          const SizedBox(height: 16),
          _assigneePanel(exc),
          if (exc.status == ApExceptionStatus.resolved ||
              exc.status == ApExceptionStatus.dismissed) ...[
            const SizedBox(height: 16),
            _resolutionPanel(exc),
          ],
          const SizedBox(height: 24),
          if (canAct) _actionButtons(exc),
        ],
      ),
    );
  }

  Widget _header(ApException exc) {
    return Row(
      children: [
        Expanded(
          child: Text(
            exc.typeLabel,
            style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
          ),
        ),
        const SizedBox(width: 8),
        ExceptionStatusBadge(status: exc.status),
        if (exc.isOverdue) ...[
          const SizedBox(width: 8),
          Text(
            'Overdue',
            style: TextStyle(
              color: Colors.red.shade700,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ],
    );
  }

  Widget _section(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: TextStyle(
            color: Colors.grey.shade700,
            fontSize: 13,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        Text(value),
      ],
    );
  }

  Widget _card({required String title, required List<Widget> children}) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              title,
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
            ),
            const SizedBox(height: 8),
            ...children,
          ],
        ),
      ),
    );
  }

  Widget _row(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 120,
            child: Text(
              label,
              style: TextStyle(color: Colors.grey.shade700, fontSize: 13),
            ),
          ),
          Expanded(child: Text(value)),
        ],
      ),
    );
  }

  Widget _invoicePanel(ApException exc) {
    return _card(
      title: 'Invoice',
      children: [
        if (exc.invoiceNumber == null && exc.vendorName == null)
          Text(
            'No linked invoice',
            style: TextStyle(color: Colors.grey.shade700),
          )
        else ...[
          if (exc.invoiceNumber != null) _row('Number', exc.invoiceNumber!),
          if (exc.vendorName != null) _row('Vendor', exc.vendorName!),
          if (exc.amount != null)
            _row('Amount', _currencyFormat.format(exc.amount)),
        ],
        _row('Severity', exc.severity.label),
      ],
    );
  }

  Widget _slaPanel(ApException exc) {
    final due = exc.dueAt;
    return _card(
      title: 'SLA',
      children: [
        _row('Created', _dateFormat.format(exc.createdAt.toLocal())),
        _row(
          'Due',
          due == null ? 'No SLA set' : _dateFormat.format(due.toLocal()),
        ),
        if (exc.isOverdue)
          _row('Status', 'Overdue')
        else if (due != null && exc.status.isActionable)
          _row('Status', 'On track'),
        if (exc.timeToResolutionHours != null)
          _row(
            'Resolved in',
            '${exc.timeToResolutionHours!.toStringAsFixed(1)} h',
          ),
      ],
    );
  }

  Widget _assigneePanel(ApException exc) {
    // The assignee picker needs the org user list, which only admins can fetch
    // (`/admin/users` is admin-only). ap_managers can still resolve/escalate/
    // dismiss; they just don't get the picker. Reassignment without that list
    // isn't safe, so gate the control on the admin role — the backend assign
    // endpoint itself is admin/ap_manager.
    final canAssign =
        AuthStore.instance.isOrgAdmin && exc.status.isActionable && !_submitting;
    return _card(
      title: 'Assignee',
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                exc.assignedTo ?? 'Unassigned',
                style: exc.assignedTo == null
                    ? TextStyle(color: Colors.grey.shade700)
                    : null,
              ),
            ),
            if (canAssign)
              TextButton.icon(
                onPressed: () => _showAssignPicker(exc),
                icon: const Icon(Icons.person_add_alt, size: 18),
                label: Text(exc.assignedTo == null ? 'Assign' : 'Reassign'),
              ),
          ],
        ),
      ],
    );
  }

  Widget _resolutionPanel(ApException exc) {
    return _card(
      title: 'Resolution',
      children: [
        if (exc.resolution != null) _row('Note', exc.resolution!),
        if (exc.resolvedBy != null) _row('By', exc.resolvedBy!),
        if (exc.resolvedAt != null)
          _row('At', _dateFormat.format(exc.resolvedAt!.toLocal())),
      ],
    );
  }

  Widget _actionButtons(ApException exc) {
    return Column(
      children: [
        Row(
          children: [
            Expanded(
              child: FilledButton.icon(
                onPressed: () => _runAction('resolve'),
                icon: const Icon(Icons.check),
                label: const Text('Resolve'),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: OutlinedButton.icon(
                onPressed: () => _runAction('escalate'),
                icon: const Icon(Icons.arrow_upward),
                label: const Text('Escalate'),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        SizedBox(
          width: double.infinity,
          child: OutlinedButton.icon(
            onPressed: () => _runAction('dismiss'),
            icon: const Icon(Icons.block),
            label: const Text('Dismiss'),
          ),
        ),
      ],
    );
  }

  Future<void> _runAction(String action) async {
    final exc = _exception;
    if (exc == null || _submitting) return;
    setState(() => _submitting = true);
    final store = ExceptionStore.instance;
    final ok = switch (action) {
      'resolve' => await store.resolve(exc.id),
      'escalate' => await store.escalate(exc.id),
      _ => await store.dismiss(exc.id),
    };
    if (!mounted) return;
    setState(() => _submitting = false);
    if (ok) {
      A11y.announce(context, 'Exception ${action}d');
      Navigator.of(context).pop(true);
    } else {
      A11y.announce(context, 'Could not $action the exception');
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not $action the exception')),
      );
    }
  }

  Future<void> _showAssignPicker(ApException exc) async {
    List<AdminUser> users;
    try {
      users = await AdminApi.listUsers(pageSize: 100);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not load users: $e')),
      );
      return;
    }
    if (!mounted) return;

    final selected = await showModalBottomSheet<({String? id, bool clear})>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) {
        // Cap the sheet at ~60% of the viewport so a long user list scrolls
        // inside the sheet instead of overflowing the screen.
        final maxHeight = MediaQuery.of(sheetContext).size.height * 0.6;
        return SafeArea(
          child: ConstrainedBox(
            constraints: BoxConstraints(maxHeight: maxHeight),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Padding(
                  padding: EdgeInsets.fromLTRB(16, 12, 16, 4),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      'Assign to',
                      style:
                          TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
                    ),
                  ),
                ),
                Flexible(
                  child: ListView(
                    shrinkWrap: true,
                    children: [
                      if (exc.assignedToUserId != null)
                        ListTile(
                          leading: const Icon(Icons.person_off_outlined),
                          title: const Text('Unassign'),
                          onTap: () => Navigator.of(sheetContext)
                              .pop((id: null, clear: true)),
                        ),
                      for (final u in users)
                        ListTile(
                          leading: CircleAvatar(child: Text(u.initial)),
                          title: Text(
                            u.fullName.isNotEmpty ? u.fullName : u.email,
                          ),
                          subtitle: Text(u.email),
                          trailing: u.id == exc.assignedToUserId
                              ? const Icon(Icons.check, color: Colors.green)
                              : null,
                          onTap: () => Navigator.of(sheetContext)
                              .pop((id: u.id, clear: false)),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );

    if (selected == null || !mounted) return;
    setState(() => _submitting = true);
    final updated = await ExceptionStore.instance.assign(
      exc.id,
      userId: selected.clear ? null : selected.id,
    );
    if (!mounted) return;
    setState(() {
      if (updated != null) _exception = updated;
      _submitting = false;
    });
    final msg = updated == null
        ? 'Could not update the assignee'
        : (updated.assignedTo == null
            ? 'Exception unassigned'
            : 'Assigned to ${updated.assignedTo}');
    A11y.announce(context, msg);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }
}
