/// A user row from the admin user-management surface
/// (`GET /api/admin/users`). Control-plane data — no tenant/business fields.
/// `roles` are the role *names* the user holds (the four system roles
/// admin/ap_manager/ap_clerk/cfo, plus any org-custom labels).
class AdminUser {
  final String id;
  final String email;
  final String fullName;
  final bool isActive;
  final List<String> roles;
  final String createdAt;

  const AdminUser({
    required this.id,
    required this.email,
    required this.fullName,
    required this.isActive,
    required this.roles,
    required this.createdAt,
  });

  factory AdminUser.fromJson(Map<String, dynamic> json) {
    final rawRoles = json['roles'];
    return AdminUser(
      id: json['id'] as String,
      email: json['email'] as String,
      fullName: json['full_name'] as String? ?? '',
      isActive: json['is_active'] as bool? ?? true,
      // The backend serializes roles as [{id, name, ...}]; carry just the name.
      roles: rawRoles is List
          ? rawRoles
              .map((r) => r is Map<String, dynamic>
                  ? (r['name'] as String? ?? '')
                  : r.toString())
              .where((s) => s.isNotEmpty)
              .toList()
          : const [],
      createdAt: json['created_at'] as String? ?? '',
    );
  }

  /// First-letter avatar seed.
  String get initial =>
      fullName.isNotEmpty ? fullName[0].toUpperCase() : '?';
}

/// A role definition from `GET /api/admin/roles`. `isSystem` marks the four
/// built-ins (admin/ap_manager/ap_clerk/cfo) that gate hardcoded routes.
class AdminRole {
  final String id;
  final String name;
  final String? description;
  final bool isSystem;

  const AdminRole({
    required this.id,
    required this.name,
    this.description,
    required this.isSystem,
  });

  factory AdminRole.fromJson(Map<String, dynamic> json) {
    return AdminRole(
      id: json['id'] as String,
      name: json['name'] as String,
      description: json['description'] as String?,
      isSystem: json['is_system'] as bool? ?? false,
    );
  }
}
