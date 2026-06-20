import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/exception.dart';
import 'package:ap_mobile/services/offline_store.dart';

class ExceptionStore extends ChangeNotifier {
  static final ExceptionStore instance = ExceptionStore._();
  ExceptionStore._();

  List<ApException> _exceptions = [];
  bool _loading = false;
  String? _error;
  String? _statusFilter;
  bool _fromCache = false;

  List<ApException> get exceptions => _exceptions;
  bool get loading => _loading;
  String? get error => _error;
  String? get statusFilter => _statusFilter;
  bool get fromCache => _fromCache;

  /// Test seam: clear all in-memory state so tests aren't coupled to the order
  /// they run in (this is a process-lifetime singleton). Not used in production.
  @visibleForTesting
  void debugReset() {
    _exceptions = [];
    _loading = false;
    _error = null;
    _statusFilter = null;
    _fromCache = false;
  }

  void setStatusFilter(String? status) {
    _statusFilter = status;
    fetch();
  }

  Future<void> fetch() async {
    _loading = true;
    _error = null;
    notifyListeners();

    final cacheKey = 'exceptions_${_statusFilter ?? 'all'}';

    try {
      _exceptions = await ExceptionApi.list(status: _statusFilter);
      _fromCache = false;
      _loading = false;

      // Cache the raw exception data for offline use.
      await OfflineStore.instance.put(
        cacheKey,
        _exceptions.map((e) => _exceptionToJson(e)).toList(),
      );

      notifyListeners();
    } catch (e) {
      // Try cache on failure.
      try {
        final cached = await OfflineStore.instance.get(cacheKey);
        if (cached != null) {
          _exceptions = (cached as List)
              .map((j) => ApException.fromJson(j as Map<String, dynamic>))
              .toList();
          _fromCache = true;
          _loading = false;
          notifyListeners();
          return;
        }
      } catch (_) {}
      // No cache to fall back on — make sure we don't keep claiming the
      // (now absent) data came from cache.
      _fromCache = false;
      _loading = false;
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<bool> resolve(String id, {String resolution = 'Resolved on mobile'}) =>
      _act(id, action: 'resolve', resolution: resolution);

  Future<bool> escalate(
    String id, {
    String resolution = 'Escalated on mobile',
  }) =>
      _act(id, action: 'escalate', resolution: resolution);

  Future<bool> dismiss(String id, {String resolution = 'Dismissed on mobile'}) =>
      _act(id, action: 'dismiss', resolution: resolution);

  Future<bool> _act(
    String id, {
    required String action,
    required String resolution,
  }) async {
    try {
      await ExceptionApi.act(id, action: action, resolution: resolution);
      await fetch();
      return true;
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return false;
    }
  }

  Map<String, dynamic> _exceptionToJson(ApException e) => {
        'id': e.id,
        'invoice_id': e.invoiceId,
        'invoice_number': e.invoiceNumber,
        'vendor_name': e.vendorName,
        'amount': e.amount,
        'exception_type': e.exceptionType,
        'type_label': e.typeLabel,
        'severity': e.severity.value,
        'description': e.description,
        'status': e.status.value,
        'resolution': e.resolution,
        'assigned_to': e.assignedTo,
        'is_overdue': e.isOverdue,
        'created_at': e.createdAt.toIso8601String(),
      };
}
