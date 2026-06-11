import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart';
import 'package:sqflite/sqflite.dart';

/// Local SQLite cache for offline viewing of invoices and dashboard data.
class OfflineStore {
  static final OfflineStore instance = OfflineStore._();
  OfflineStore._();

  Database? _db;

  Future<Database> get db async {
    _db ??= await _initDb();
    return _db!;
  }

  Future<Database> _initDb() async {
    final dbPath = await getDatabasesPath();
    return openDatabase(
      join(dbPath, 'ap_cache.db'),
      version: 1,
      onCreate: _createSchema,
    );
  }

  static Future<void> _createSchema(Database db, int version) async {
    await db.execute('''
      CREATE TABLE cache (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
      )
    ''');
  }

  /// Test seam: back this store with a fresh private in-memory database so
  /// parallel test isolates don't contend on the shared on-disk cache file.
  /// Not used by production code.
  @visibleForTesting
  Future<void> debugUseInMemory() async {
    _db = await databaseFactory.openDatabase(
      inMemoryDatabasePath,
      options: OpenDatabaseOptions(version: 1, onCreate: _createSchema),
    );
  }

  Future<void> put(String key, dynamic value) async {
    final database = await db;
    await database.insert(
      'cache',
      {
        'key': key,
        'value': jsonEncode(value),
        'updated_at': DateTime.now().millisecondsSinceEpoch,
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<dynamic> get(String key) async {
    final database = await db;
    final rows = await database.query(
      'cache',
      where: 'key = ?',
      whereArgs: [key],
    );
    if (rows.isEmpty) return null;
    return jsonDecode(rows.first['value'] as String);
  }

  Future<void> clear() async {
    final database = await db;
    await database.delete('cache');
  }

  /// Cache API response and return it. On failure, return the cached version
  /// (if any). The returned record's [fromCache] flag tells the caller whether
  /// the live fetch succeeded (`false`) or stale cache was served (`true`), so
  /// the UI can surface an "offline / showing cached data" indicator.
  Future<({T data, bool fromCache})> cachedFetch<T>({
    required String key,
    required Future<T> Function() fetch,
    required T Function(dynamic json) fromCache,
    required dynamic Function(T data) toCache,
  }) async {
    try {
      final data = await fetch();
      await put(key, toCache(data));
      return (data: data, fromCache: false);
    } catch (e) {
      debugPrint('[offline] Fetch failed for $key, trying cache: $e');
      final cached = await get(key);
      if (cached != null) {
        debugPrint('[offline] Serving $key from cache');
        return (data: fromCache(cached), fromCache: true);
      }
      rethrow;
    }
  }
}
