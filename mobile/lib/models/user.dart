class User {
  final String id;
  final String email;
  final String fullName;
  final String organizationId;
  final List<String> roles;

  User({
    required this.id,
    required this.email,
    required this.fullName,
    required this.organizationId,
    required this.roles,
  });

  factory User.fromJson(Map<String, dynamic> json) {
    return User(
      id: json['id'] as String,
      email: json['email'] as String,
      fullName: json['full_name'] as String,
      organizationId: json['organization_id'] as String,
      roles: (json['roles'] as List<dynamic>).cast<String>(),
    );
  }

  bool hasRole(String role) => roles.contains(role);
  bool get isAdmin => hasRole('admin');
  bool get isManager => hasRole('ap_manager');
  bool get isCfo => hasRole('cfo');
  bool get isClerkOnly =>
      roles.length == 1 && roles.first == 'ap_clerk';
}
