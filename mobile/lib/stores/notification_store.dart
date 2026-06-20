import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/notification.dart';
import 'package:ap_mobile/services/offline_store.dart';

/// In-app notification center state. Mirrors [ExceptionStore]: a process-lifetime
/// `ChangeNotifier` singleton over `GET /api/notifications` + mark-read /
/// read-all, with an offline-cached list and a separately-tracked unread count
/// (the badge) so the app-bar bell can render without forcing a full fetch.
class NotificationStore extends ChangeNotifier {
  static final NotificationStore instance = NotificationStore._();
  NotificationStore._();

  List<AppNotification> _notifications = [];
  int _unread = 0;
  bool _loading = false;
  String? _error;
  bool _unreadOnly = false;
  bool _fromCache = false;

  List<AppNotification> get notifications => _notifications;
  int get unread => _unread;
  bool get loading => _loading;
  String? get error => _error;
  bool get unreadOnly => _unreadOnly;
  bool get fromCache => _fromCache;

  /// Test seam: clear all in-memory state so tests aren't coupled to the order
  /// they run in (this is a process-lifetime singleton). Not used in production.
  @visibleForTesting
  void debugReset() {
    _notifications = [];
    _unread = 0;
    _loading = false;
    _error = null;
    _unreadOnly = false;
    _fromCache = false;
  }

  void setUnreadOnly(bool value) {
    _unreadOnly = value;
    fetch();
  }

  /// Cheap badge refresh — no list reload. Best-effort: a failure leaves the
  /// last known count in place (the bell never blocks the app).
  Future<void> refreshUnreadCount() async {
    try {
      _unread = await NotificationApi.unreadCount();
      notifyListeners();
    } catch (_) {
      // Keep the stale count; the next full fetch will reconcile it.
    }
  }

  Future<void> fetch() async {
    _loading = true;
    _error = null;
    notifyListeners();

    final cacheKey = 'notifications_${_unreadOnly ? 'unread' : 'all'}';

    try {
      final pageData = await NotificationApi.list(unreadOnly: _unreadOnly);
      _notifications = pageData.items;
      _unread = pageData.unread;
      _fromCache = false;
      _loading = false;

      await OfflineStore.instance.put(
        cacheKey,
        _notifications.map((n) => n.toJson()).toList(),
      );

      notifyListeners();
    } catch (e) {
      // Try cache on failure (offline mode — same pattern as ExceptionStore).
      try {
        final cached = await OfflineStore.instance.get(cacheKey);
        if (cached != null) {
          _notifications = (cached as List)
              .map((j) => AppNotification.fromJson(j as Map<String, dynamic>))
              .toList();
          _unread = _notifications.where((n) => !n.isRead).length;
          _fromCache = true;
          _loading = false;
          notifyListeners();
          return;
        }
      } catch (_) {}
      _fromCache = false;
      _loading = false;
      _error = e.toString();
      notifyListeners();
    }
  }

  /// Mark one notification read. Optimistically flips the row + decrements the
  /// badge before the request so the UI reacts instantly; on failure we surface
  /// the error and reconcile via a fetch. Idempotent — re-marking a read row is
  /// a no-op locally and server-side.
  Future<bool> markRead(String id) async {
    final idx = _notifications.indexWhere((n) => n.id == id);
    if (idx == -1) return false;
    final existing = _notifications[idx];
    if (existing.isRead) return true; // already read — nothing to do

    _notifications[idx] = existing.copyMarkedRead(DateTime.now());
    if (_unread > 0) _unread--;
    notifyListeners();

    try {
      await NotificationApi.markRead(id);
      // If we're filtered to unread-only, the now-read row should drop out.
      if (_unreadOnly) {
        _notifications.removeWhere((n) => n.id == id);
        notifyListeners();
      }
      return true;
    } catch (e) {
      // Reconcile real state — undo the optimistic flip via a refetch. fetch()
      // clears _error on entry, so re-stamp it afterwards (the mark-read is the
      // failure the caller cares about, not the reconciling read).
      final message = e.toString();
      await fetch();
      _error = message;
      notifyListeners();
      return false;
    }
  }

  /// Mark every unread notification read.
  Future<bool> markAllRead() async {
    try {
      await NotificationApi.markAllRead();
      await fetch();
      return true;
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return false;
    }
  }
}
