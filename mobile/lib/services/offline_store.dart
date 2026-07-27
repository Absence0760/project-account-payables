import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart';
import 'package:sqflite/sqflite.dart';

/// Local SQLite cache for offline viewing of invoices and dashboard data.
///
/// **Session scoping (tenant isolation).** The cache is a device-local copy of
/// tenant financial data, so it must never outlive — or cross — the session
/// that wrote it. Every row is namespaced by the `(tenant slug, user id)` that
/// wrote it: [put] / [get] prepend the scope prefix themselves, so a call site
/// never has to remember one (and one that forgets can't reintroduce the leak).
/// Two belts:
///
/// 1. **Namespace** — a different `(tenant, user)` derives a different key, so
///    it cannot read a prior session's rows even if a clear was missed.
/// 2. **Purge** — [setScope] wipes the whole cache whenever the scope differs
///    from the one last persisted (a different user / tenant signing in, or an
///    install upgrading from the pre-scoping schema), and
///    `SessionManager.endSession` clears it on every logout / forced logout.
///
/// With **no** scope set (signed out, or the auth layer never handed one over)
/// the store is inert: [put] drops the write and [get] returns null. That
/// fails closed — the worst case is no offline cache, never another session's
/// data.
class OfflineStore {
  static final OfflineStore instance = OfflineStore._();
  OfflineStore._();

  /// Bumped to 2 when session scoping landed. The upgrade purges every
  /// pre-existing (un-namespaced) row on an installed app's first open, so a
  /// device carrying a previous release's cache can't serve those rows either.
  static const int _dbVersion = 2;

  /// Row holding the scope the cache was last written under.
  ///
  /// Collision-free by construction: every real row's key starts with
  /// `<encoded tenant>|<encoded user>|`, and `Uri.encodeComponent` percent-
  /// encodes `#` (it escapes everything outside `A-Za-z0-9-_.!~*'()`), so no
  /// scoped key can ever begin with `#`. Plain printable ASCII on purpose —
  /// it round-trips through SQLite TEXT and keeps this file a readable text
  /// diff. Guarded by `test/services/session_test.dart`.
  static const String _scopeMetaKey = '#session_scope';

  Database? _db;

  /// When non-null, the store is backed by a pure in-memory map instead of
  /// SQLite (see [debugUseMemory]). Values are stored JSON-encoded exactly as
  /// the SQLite path stores them, so behaviour (incl. serialization fidelity)
  /// matches production.
  Map<String, String>? _memory;

  /// `<tenant>|<user>|` — prepended to every key. Null when signed out.
  String? _scopePrefix;

  Future<Database> get db async {
    _db ??= await _initDb();
    return _db!;
  }

  Future<Database> _initDb() async {
    final dbPath = await getDatabasesPath();
    return openDatabase(
      join(dbPath, 'feohledger_cache.db'),
      version: _dbVersion,
      onCreate: _createSchema,
      onUpgrade: _upgradeSchema,
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

  /// v1 → v2: rows written before session scoping have global keys
  /// (`dashboard`, `invoices_all_`, …) and no owner, so there is no safe way to
  /// attribute them to a session. Drop them all — the app refetches.
  static Future<void> _upgradeSchema(Database db, int from, int to) async {
    if (from < 2) {
      await db.delete('cache');
    }
  }

  /// Test seam: back this store with a pure in-memory map instead of SQLite.
  /// Not used by production code. Necessary because sqflite's internal
  /// synchronization Timer never resolves inside `testWidgets`' fake-async
  /// zone (it leaks as a pending timer and stalls any fetch that writes the
  /// cache); a plain map resolves on the microtask queue, so it works under
  /// both `test()` and `testWidgets()` and needs no native plugin.
  ///
  /// Also installs a default session scope so cache-backed tests behave like a
  /// signed-in app; pass [tenantSlug] / [userId] to simulate a specific
  /// session.
  @visibleForTesting
  void debugUseMemory({
    String tenantSlug = 'test-tenant',
    String userId = 'test-user',
  }) {
    _memory = {};
    _db = null;
    _scopePrefix = _prefixFor(tenantSlug, userId);
    _memory![_scopeMetaKey] = _scopePrefix!;
  }

  /// Test seam: close the SQLite handle and drop the scope, so a test can
  /// re-open the database from cold (e.g. to exercise the v1 → v2 upgrade).
  /// Not used by production code.
  @visibleForTesting
  Future<void> debugClose() async {
    await _db?.close();
    _db = null;
    _memory = null;
    _scopePrefix = null;
  }

  /// True when a session scope is installed (i.e. reads/writes are allowed).
  bool get hasScope => _scopePrefix != null;

  static String _prefixFor(String tenantSlug, String userId) =>
      '${Uri.encodeComponent(tenantSlug)}|${Uri.encodeComponent(userId)}|';

  /// Bind the cache to the signed-in `(tenant, user)`. Returns true when the
  /// scope differs from the one the cache was last written under — in which
  /// case every existing row is purged first, so a device reused by another
  /// user (or upgraded from the un-namespaced schema) starts empty.
  ///
  /// Called from the auth layer only (`SessionManager.beginSession`).
  Future<bool> setScope({
    required String tenantSlug,
    required String userId,
  }) async {
    final next = _prefixFor(tenantSlug, userId);
    var changed = true;
    try {
      final previous = await _rawGet(_scopeMetaKey);
      changed = previous != next;
      if (changed) {
        await _rawClear();
        await _rawPut(_scopeMetaKey, next);
      }
    } catch (e) {
      // The cache being unavailable must never block sign-in. The namespace
      // (below) is what enforces isolation; the purge is the second belt.
      debugPrint('[offline] Could not reconcile cache scope: $e');
    }
    _scopePrefix = next;
    return changed;
  }

  /// Drop the session scope (sign-out). Subsequent reads/writes are inert
  /// until a new scope is installed.
  void clearScope() {
    _scopePrefix = null;
  }

  Future<void> put(String key, dynamic value) async {
    final scoped = _scopedKey(key);
    if (scoped == null) return;
    await _rawPut(scoped, jsonEncode(value));
  }

  Future<dynamic> get(String key) async {
    final scoped = _scopedKey(key);
    if (scoped == null) return null;
    final raw = await _rawGet(scoped);
    return raw == null ? null : jsonDecode(raw);
  }

  /// Wipe every cached row (all sessions). Used on logout and on a scope
  /// change.
  Future<void> clear() async => _rawClear();

  String? _scopedKey(String key) {
    final prefix = _scopePrefix;
    if (prefix == null) {
      debugPrint('[offline] No session scope — cache read/write skipped');
      return null;
    }
    return '$prefix$key';
  }

  Future<void> _rawPut(String key, String encoded) async {
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

  Future<String?> _rawGet(String key) async {
    if (_memory != null) return _memory![key];
    final database = await db;
    final rows = await database.query(
      'cache',
      where: 'key = ?',
      whereArgs: [key],
    );
    if (rows.isEmpty) return null;
    return rows.first['value'] as String;
  }

  Future<void> _rawClear() async {
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
      debugPrint('[offline] Fetch failed, trying cache: $e');
      final cached = await get(key);
      if (cached != null) {
        debugPrint('[offline] Serving cached copy');
        return (data: fromCache(cached), fromCache: true);
      }
      rethrow;
    }
  }
}
