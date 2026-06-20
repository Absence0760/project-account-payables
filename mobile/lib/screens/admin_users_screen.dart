import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'package:ap_mobile/models/admin_user.dart';
import 'package:ap_mobile/stores/admin_user_store.dart';
import 'package:ap_mobile/stores/auth_store.dart';
import 'package:ap_mobile/utils/a11y.dart';

/// Admin — user management. Lists the org's users (search by name/email) and
/// lets an admin change a user's roles or activate / deactivate them over
/// `PATCH /api/admin/users/{id}`. Admin-only (the route is
/// `require_roles(ROLE_ADMIN)`); the Settings entry point is hidden for
/// everyone else, and this screen guards again on build.
class AdminUsersScreen extends StatefulWidget {
  const AdminUsersScreen({super.key});

  @override
  State<AdminUsersScreen> createState() => _AdminUsersScreenState();
}

class _AdminUsersScreenState extends State<AdminUsersScreen> {
  final _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    SchedulerBinding.instance.addPostFrameCallback((_) {
      AdminUserStore.instance.fetch();
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
        title: const Text('User Management'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(56),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: SearchBar(
              controller: _searchController,
              hintText: 'Search by name or email',
              leading: const Icon(Icons.search, size: 20),
              onChanged: (q) => AdminUserStore.instance.setSearch(q),
              elevation: WidgetStateProperty.all(0),
            ),
          ),
        ),
      ),
      body: ListenableBuilder(
        listenable: AdminUserStore.instance,
        builder: (context, _) {
          final store = AdminUserStore.instance;

          if (store.loading && store.users.isEmpty) {
            return const Center(child: CircularProgressIndicator());
          }
          if (store.error != null && store.users.isEmpty) {
            return _ErrorState(message: store.error!, onRetry: store.fetch);
          }
          if (store.users.isEmpty) {
            return const Center(child: Text('No users found'));
          }

          return RefreshIndicator(
            onRefresh: store.fetch,
            child: ListView.separated(
              itemCount: store.users.length,
              separatorBuilder: (_, _) => const Divider(height: 1),
              itemBuilder: (context, index) {
                final user = store.users[index];
                return _UserTile(
                  user: user,
                  onTap: () => _showActions(user),
                );
              },
            ),
          );
        },
      ),
    );
  }

  void _showActions(AdminUser user) {
    final isSelf = AuthStore.instance.user?.id == user.id;
    showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) {
        return SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              ListTile(
                title: Text(
                  user.fullName,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                subtitle: Text(user.email),
              ),
              const Divider(height: 1),
              ListTile(
                leading: const Icon(Icons.badge_outlined),
                title: const Text('Edit roles'),
                subtitle: Text(
                  user.roles.isEmpty ? 'No roles' : user.roles.join(', '),
                ),
                onTap: () {
                  Navigator.of(sheetContext).pop();
                  _editRoles(user);
                },
              ),
              ListTile(
                leading: Icon(
                  user.isActive ? Icons.block : Icons.check_circle_outline,
                  color: user.isActive ? Colors.red.shade700 : Colors.green,
                ),
                title: Text(user.isActive ? 'Deactivate user' : 'Activate user'),
                subtitle: isSelf
                    ? const Text("You can't deactivate your own account")
                    : Text(
                        user.isActive
                            ? 'Signs them out and blocks sign-in'
                            : 'Restores sign-in access',
                      ),
                // The backend lets an admin deactivate anyone, but locking
                // yourself out would be a footgun — disable it for self.
                enabled: !(isSelf && user.isActive),
                onTap: () {
                  Navigator.of(sheetContext).pop();
                  _toggleActive(user);
                },
              ),
            ],
          ),
        );
      },
    );
  }

  Future<void> _editRoles(AdminUser user) async {
    final available = AdminUserStore.instance.systemRoleNames;
    final selected = await showModalBottomSheet<List<String>>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => _RoleEditor(
        available: available,
        initial: user.roles.where(available.contains).toList(),
      ),
    );
    if (selected == null || !mounted) return;

    final ok = await AdminUserStore.instance.setRoles(user.id, selected);
    if (!mounted) return;
    final message = ok
        ? 'Updated roles for ${user.fullName}'
        : 'Failed to update roles: ${AdminUserStore.instance.error ?? ''}';
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
    A11y.announce(context, message);
  }

  Future<void> _toggleActive(AdminUser user) async {
    final target = !user.isActive;
    final ok = await AdminUserStore.instance.setActive(user.id, target);
    if (!mounted) return;
    final verb = target ? 'Activated' : 'Deactivated';
    final message = ok
        ? '$verb ${user.fullName}'
        : 'Failed to update ${user.fullName}: '
            '${AdminUserStore.instance.error ?? ''}';
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
    A11y.announce(context, message);
  }
}

/// Multi-select role editor — a checkbox per available system role with an
/// Apply / Cancel footer. Returns the chosen role-name list (Apply) or null
/// (Cancel / dismiss).
class _RoleEditor extends StatefulWidget {
  final List<String> available;
  final List<String> initial;

  const _RoleEditor({required this.available, required this.initial});

  @override
  State<_RoleEditor> createState() => _RoleEditorState();
}

class _RoleEditorState extends State<_RoleEditor> {
  late final Set<String> _selected = {...widget.initial};

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text(
              'Edit roles',
              style: TextStyle(fontWeight: FontWeight.w600, fontSize: 16),
            ),
          ),
          const Divider(height: 1),
          for (final role in widget.available)
            CheckboxListTile(
              value: _selected.contains(role),
              title: Text(role),
              onChanged: (checked) => setState(() {
                if (checked == true) {
                  _selected.add(role);
                } else {
                  _selected.remove(role);
                }
              }),
            ),
          const Divider(height: 1),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('Cancel'),
                ),
                const SizedBox(width: 8),
                FilledButton(
                  onPressed: () =>
                      Navigator.of(context).pop(_selected.toList()),
                  child: const Text('Apply'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _UserTile extends StatelessWidget {
  final AdminUser user;
  final VoidCallback onTap;

  const _UserTile({required this.user, required this.onTap});

  String get _semanticLabel {
    final parts = <String>[
      user.fullName,
      user.email,
      if (user.roles.isNotEmpty) 'roles ${user.roles.join(', ')}',
      user.isActive ? 'active' : 'inactive',
    ];
    return parts.join(', ');
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: _semanticLabel,
      button: true,
      excludeSemantics: true,
      child: ListTile(
        onTap: onTap,
        leading: CircleAvatar(
          backgroundColor: user.isActive ? Colors.blue : Colors.grey.shade500,
          child: Text(
            user.initial,
            style: const TextStyle(color: Colors.white),
          ),
        ),
        title: Text(
          user.fullName,
          style: const TextStyle(fontWeight: FontWeight.w600),
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              user.email,
              style: TextStyle(color: Colors.grey.shade700, fontSize: 13),
              overflow: TextOverflow.ellipsis,
            ),
            const SizedBox(height: 4),
            Wrap(
              spacing: 6,
              runSpacing: 4,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                if (!user.isActive)
                  _InactiveBadge(),
                ...user.roles.map(
                  (r) => Chip(
                    label: Text(r, style: const TextStyle(fontSize: 11)),
                    visualDensity: VisualDensity.compact,
                    materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    padding: EdgeInsets.zero,
                  ),
                ),
              ],
            ),
          ],
        ),
        trailing: const Icon(Icons.chevron_right),
        isThreeLine: true,
      ),
    );
  }
}

class _InactiveBadge extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    // shade100 tint + shade900 text clears AA contrast at small sizes.
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: Colors.red.shade100,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        'Inactive',
        style: TextStyle(
          color: Colors.red.shade900,
          fontSize: 11,
          fontWeight: FontWeight.w600,
        ),
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
          const Text('Could not load users'),
          const SizedBox(height: 12),
          FilledButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    );
  }
}
