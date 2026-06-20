import 'package:flutter_test/flutter_test.dart';

import 'package:ap_mobile/models/admin_user.dart';

void main() {
  group('AdminUser.fromJson', () {
    test('extracts role names from the [{id, name}] role objects', () {
      final user = AdminUser.fromJson({
        'id': 'u1',
        'email': 'a@b.com',
        'full_name': 'Alice Admin',
        'is_active': true,
        'roles': [
          {'id': 'r1', 'name': 'admin'},
          {'id': 'r2', 'name': 'cfo'},
        ],
        'created_at': '2026-01-01T00:00:00',
      });

      expect(user.fullName, 'Alice Admin');
      expect(user.roles, ['admin', 'cfo']);
      expect(user.isActive, isTrue);
      expect(user.initial, 'A');
    });

    test('tolerates missing/empty fields', () {
      final user = AdminUser.fromJson({'id': 'u2', 'email': 'x@y.com'});

      expect(user.fullName, '');
      expect(user.roles, isEmpty);
      expect(user.isActive, isTrue);
      expect(user.initial, '?');
    });
  });

  group('AdminRole.fromJson', () {
    test('parses is_system', () {
      final role = AdminRole.fromJson({
        'id': 'r1',
        'name': 'admin',
        'description': 'Full access',
        'is_system': true,
      });

      expect(role.name, 'admin');
      expect(role.isSystem, isTrue);
      expect(role.description, 'Full access');
    });
  });
}
