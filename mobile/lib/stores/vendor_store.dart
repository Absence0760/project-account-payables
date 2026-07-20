import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/vendor.dart';
import 'package:ap_mobile/services/offline_store.dart';

/// Vendor list + verify/reject/ERP-sync actions. Mirrors [ContractStore]:
/// in-memory singleton, offline-cached list, action methods refetch on success.
class VendorStore extends ChangeNotifier {
  static final VendorStore instance = VendorStore._();
  VendorStore._();

  List<Vendor> _vendors = [];
  bool _loading = false;
  String? _error;
  String? _statusFilter;
  String? _searchQuery;
  bool _fromCache = false;

  List<Vendor> get vendors => _vendors;
  bool get loading => _loading;
  String? get error => _error;
  String? get statusFilter => _statusFilter;
  String? get searchQuery => _searchQuery;
  bool get fromCache => _fromCache;

  /// Drop all in-memory state. Called on logout / forced logout through
  /// `SessionManager.endSession` — these are process-lifetime singletons, so
  /// without this a signed-out user's data would still be in memory for the
  /// next account on the device. Tests use it to decouple from run order.
  void reset() {
    _vendors = [];
    _loading = false;
    _error = null;
    _statusFilter = null;
    _searchQuery = null;
    _fromCache = false;
  }

  void setStatusFilter(String? status) {
    _statusFilter = status;
    fetch();
  }

  void setSearch(String? query) {
    _searchQuery = query;
    fetch();
  }

  Future<void> fetch() async {
    _loading = true;
    _error = null;
    notifyListeners();

    final cacheKey = 'vendors_${_statusFilter ?? 'all'}_${_searchQuery ?? ''}';

    try {
      _vendors = await VendorApi.list(
        status: _statusFilter,
        search: _searchQuery,
      );
      _fromCache = false;
      _loading = false;

      await OfflineStore.instance.put(
        cacheKey,
        _vendors.map(_vendorToJson).toList(),
      );

      notifyListeners();
    } catch (e) {
      try {
        final cached = await OfflineStore.instance.get(cacheKey);
        if (cached != null) {
          _vendors = (cached as List)
              .map((j) => Vendor.fromJson(j as Map<String, dynamic>))
              .toList();
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

  Future<bool> verify(String id) => _act(() => VendorApi.verify(id));

  Future<bool> reject(String id) => _act(() => VendorApi.reject(id));

  Future<bool> _act(Future<void> Function() action) async {
    try {
      await action();
      await fetch();
      return true;
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return false;
    }
  }

  /// Pull vendors from the connected ERP, then refetch the list. Returns the
  /// server's human-readable summary message on success, or null on failure
  /// (with [error] set). A 400 (no ERP configured) is a failure here.
  Future<String?> syncErp() async {
    try {
      final result = await VendorApi.syncErp();
      await fetch();
      return result['message'] as String? ?? 'ERP sync complete';
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return null;
    }
  }

  Map<String, dynamic> _vendorToJson(Vendor v) => {
        'id': v.id,
        'name': v.name,
        'code': v.code,
        'email': v.email,
        'phone': v.phone,
        'status': v.status.value,
        'source': v.source,
        'payment_terms': v.paymentTerms,
        'verified_by': v.verifiedBy,
        'erp_vendor_id': v.erpVendorId,
        'invoice_count': v.invoiceCount,
      };
}
