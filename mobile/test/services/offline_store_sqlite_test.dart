import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:feohledger_mobile/services/offline_store.dart';

/// Session scoping against the **real SQLite backend** (issue #176).
///
/// The rest of the suite drives [OfflineStore] through its in-memory test
/// seam, which can't prove the scope prefix and the `#session_scope` sentinel
/// survive a round trip through a SQLite TEXT column, nor that the v1 → v2
/// upgrade actually purges an existing device's un-namespaced rows. This file
/// runs the production code path on a real database via `sqflite_common_ffi`.

Future<String> _dbPath() async => join(await getDatabasesPath(), 'feohledger_cache.db');

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  setUp(() async {
    await OfflineStore.instance.debugClose();
    await databaseFactory.deleteDatabase(await _dbPath());
  });

  tearDownAll(() async {
    await OfflineStore.instance.debugClose();
  });

  test('scoped keys round-trip through a real SQLite TEXT column', () async {
    await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');
    await OfflineStore.instance.put('invoices_all_', [
      {'id': 'inv-1', 'amount': '1234.56'},
    ]);

    final cached = await OfflineStore.instance.get('invoices_all_');
    expect((cached as List).first['amount'], '1234.56');

    // A different session on the same physical database.
    await OfflineStore.instance.setScope(tenantSlug: 'globex', userId: 'u2');
    expect(await OfflineStore.instance.get('invoices_all_'), isNull);

    // ...and the first session's rows were purged, not merely hidden.
    await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');
    expect(await OfflineStore.instance.get('invoices_all_'), isNull);
  });

  test('the scope sentinel row cannot be clobbered by a cached key', () async {
    await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');
    // A caller using the sentinel's exact text as a cache key still writes to
    // its own namespaced row, so the scope bookkeeping survives.
    await OfflineStore.instance.put('#session_scope', {'total_invoices': 1});

    final unchanged = await OfflineStore.instance.setScope(
      tenantSlug: 'acme',
      userId: 'u1',
    );

    expect(unchanged, isFalse, reason: 'scope meta must still read as acme|u1');
    expect(await OfflineStore.instance.get('#session_scope'), isNotNull);
  });

  test('a v1 cache is purged on upgrade — legacy rows are unreadable',
      () async {
    // Stand up the pre-scoping schema exactly as the shipped v1 did, with a
    // global un-namespaced row a previous user would have left behind.
    final legacy = await databaseFactory.openDatabase(
      await _dbPath(),
      options: OpenDatabaseOptions(
        version: 1,
        onCreate: (db, version) => db.execute('''
          CREATE TABLE cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
          )
        '''),
      ),
    );
    await legacy.insert('cache', {
      'key': 'dashboard',
      'value': '{"total_invoices":42}',
      'updated_at': 0,
    });
    await legacy.close();

    // Next launch: the production code opens it and runs the upgrade.
    await OfflineStore.instance.setScope(tenantSlug: 'acme', userId: 'u1');
    expect(await OfflineStore.instance.get('dashboard'), isNull);

    // Assert at the storage layer that the row is gone, not just unreachable.
    final db = await OfflineStore.instance.db;
    final rows = await db.query('cache', where: 'key = ?', whereArgs: ['dashboard']);
    expect(rows, isEmpty, reason: 'v1 rows must be deleted by the v2 upgrade');
  });
}
