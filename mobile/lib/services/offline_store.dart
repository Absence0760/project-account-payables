import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart';
import 'package:sqflite/sqflite.dart';

/// Local SQLite cache for offline viewing of invoices and dashboard data.
class OfflineStore {
  static final OfflineStore instance = OfflineStore._();
  OfflineStore._();

  Database? _db;

  /// When non-null, the store is backed by a pure in-memory map instead of
  /// SQLite (see [debugUseMemory]). Values are stored JSON-encoded exactly as
  /// the SQLite path stores them, so behaviour (incl. serialization fidelity)
  /// matches production.
  Map<String, String>? _memory;

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

  /// Test seam: back this store with a pure in-memory map instead of SQLite.
  /// Not used by production code. Necessary because sqflite's internal
  /// synchronization Timer never resolves inside `testWidgets`' fake-async
  /// zone (it leaks as a pending timer and stalls any fetch that writes the
  /// cache); a plain map resolves on the microtask queue, so it works under
  /// both `test()` and `testWidgets()` and needs no native plugin.
  @visibleForTesting
  void debugUseMemory() {
    _memory = {};
    _db = null;
  }

  Future<void> put(String key, dynamic value) async {
    final encoded = jsonEncode(value);
    if (_memory != null) {
      _memory![key] = encoded;
      return;
    }
    final database = await db;
    await database.insert(
      'cache',
      {
        'key': key,
        'value': encoded,
        'updated_at': DateTime.now().millisecondsSinceEpoch,
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<dynamic> get(String key) async {
    if (_memory != null) {
      final raw = _memory![key];
      return raw == null ? null : jsonDecode(raw);
    }
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
    if (_memory != null) {
      _memory!.clear();
      return;
    }
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
