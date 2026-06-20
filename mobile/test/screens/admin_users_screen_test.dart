import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/screens/admin_users_screen.dart';
import 'package:ap_mobile/stores/admin_user_store.dart';

http.Response _json(Object body, [int status = 200]) => http.Response(
      jsonEncode(body),
      status,
      headers: {'content-type': 'application/json'},
    );

http.Response _users(List<Map<String, dynamic>> items) =>
    _json({'items': items, 'total': items.length, 'page': 1});

http.Response _roles() => _json([
      {'id': 'r1', 'name': 'admin', 'is_system': true},
      {'id': 'r2', 'name': 'ap_manager', 'is_system': true},
      {'id': 'r3', 'name': 'ap_clerk', 'is_system': true},
      {'id': 'r4', 'name': 'cfo', 'is_system': true},
    ]);

Map<String, dynamic> _userJson(
  String id, {
  String name = 'User',
  bool active = true,
  List<String> roles = const ['ap_clerk'],
}) =>
    {
      'id': id,
      'email': 'u$id@example.com',
      'full_name': '$name $id',
      'is_active': active,
      'roles': roles.map((r) => {'id': 'role-$r', 'name': r}).toList(),
      'created_at': '2026-01-01T12:00:00',
    };

MockClient _client(List<Map<String, dynamic>> users) => MockClient((req) async {
      if (req.url.path.endsWith('/admin/roles')) return _roles();
      return _users(users);
    });

Future<void> _pumpUntil(WidgetTester tester, Finder finder) async {
  for (var i = 0; i < 20 && finder.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    AdminUserStore.instance.debugReset();
    FlutterSecureStorage.setMockInitialValues({});
    ApiClient().debugConfigure();
  });

  testWidgets('lists each org user once loaded', (tester) async {
    ApiClient().debugConfigure(
      client: _client([_userJson('1'), _userJson('2', active: false)]),
    );

    await tester.pumpWidget(const MaterialApp(home: AdminUsersScreen()));
    await _pumpUntil(tester, find.text('User 1'));

    expect(find.text('User 1'), findsOneWidget);
    expect(find.text('User 2'), findsOneWidget);
    // Deactivated users carry the Inactive badge.
    expect(find.text('Inactive'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('tapping a user opens the action sheet', (tester) async {
    ApiClient().debugConfigure(client: _client([_userJson('1')]));

    await tester.pumpWidget(const MaterialApp(home: AdminUsersScreen()));
    await _pumpUntil(tester, find.text('User 1'));

    await tester.tap(find.text('User 1'));
    await tester.pumpAndSettle();

    expect(find.text('Edit roles'), findsOneWidget);
    expect(find.text('Deactivate user'), findsOneWidget);
  });

  testWidgets('a meaningful error surfaces a Retry affordance', (tester) async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => http.Response('boom', 500)),
    );

    await tester.pumpWidget(const MaterialApp(home: AdminUsersScreen()));
    await _pumpUntil(tester, find.text('Could not load users'));

    expect(find.widgetWithText(FilledButton, 'Retry'), findsOneWidget);
  });

  testWidgets('editing roles PATCHes the chosen role set', (tester) async {
    Map<String, dynamic>? patchBody;
    ApiClient().debugConfigure(
      client: MockClient((req) async {
        if (req.method == 'PATCH') {
          patchBody = jsonDecode(req.body) as Map<String, dynamic>;
          return _json(_userJson('1', roles: ['ap_manager']));
        }
        if (req.url.path.endsWith('/admin/roles')) return _roles();
        return _users([_userJson('1')]);
      }),
    );

    await tester.pumpWidget(const MaterialApp(home: AdminUsersScreen()));
    await _pumpUntil(tester, find.text('User 1'));

    await tester.tap(find.text('User 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Edit roles'));
    await tester.pumpAndSettle();

    // Toggle ap_manager on (ap_clerk is already checked from the seed).
    await tester.ensureVisible(find.text('ap_manager'));
    await tester.tap(find.text('ap_manager'));
    await tester.pump();
    await tester.ensureVisible(find.widgetWithText(FilledButton, 'Apply'));
    await tester.tap(find.widgetWithText(FilledButton, 'Apply'));
    await tester.pumpAndSettle();

    expect(patchBody, isNotNull);
    expect(
      (patchBody!['role_names'] as List).toSet(),
      {'ap_clerk', 'ap_manager'},
    );
  });
}
