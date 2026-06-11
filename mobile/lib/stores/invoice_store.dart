import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/services/offline_store.dart';

class InvoiceStore extends ChangeNotifier {
  static final InvoiceStore instance = InvoiceStore._();
  InvoiceStore._();

  List<Invoice> _invoices = [];
  bool _loading = false;
  String? _error;
  String? _statusFilter;
  String? _searchQuery;
  bool _fromCache = false;

  List<Invoice> get invoices => _invoices;
  bool get loading => _loading;
  String? get error => _error;
  String? get statusFilter => _statusFilter;
  bool get fromCache => _fromCache;

  List<Invoice> get pendingApproval =>
      _invoices.where((i) => i.status == InvoiceStatus.readyForReview).toList();

  /// Test seam: clear all in-memory state so tests aren't coupled to the order
  /// they run in (this is a process-lifetime singleton). Not used in production.
  @visibleForTesting
  void debugReset() {
    _invoices = [];
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

    final cacheKey = 'invoices_${_statusFilter ?? 'all'}_${_searchQuery ?? ''}';

    try {
      _invoices = await InvoiceApi.list(
        status: _statusFilter,
        search: _searchQuery,
      );
      _fromCache = false;
      _loading = false;

      // Cache the raw invoice data for offline use
      await OfflineStore.instance.put(
        cacheKey,
        _invoices.map((i) => _invoiceToJson(i)).toList(),
      );

      notifyListeners();
    } catch (e) {
      // Try cache on failure
      try {
        final cached = await OfflineStore.instance.get(cacheKey);
        if (cached != null) {
          _invoices = (cached as List)
              .map((j) => Invoice.fromJson(j as Map<String, dynamic>))
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

  Future<bool> approve(String id) async {
    try {
      await InvoiceApi.approve(id);
      await fetch();
      return true;
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return false;
    }
  }

  Future<bool> reject(String id, String reason) async {
    try {
      await InvoiceApi.reject(id, reason);
      await fetch();
      return true;
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return false;
    }
  }

  Map<String, dynamic> _invoiceToJson(Invoice i) => {
        'id': i.id,
        'invoice_number': i.invoiceNumber,
        'vendor_name': i.vendorName,
        'amount': i.amount,
        'currency': i.currency,
        'status': i.status.value,
        'invoice_date': i.invoiceDate?.toIso8601String(),
        'due_date': i.dueDate?.toIso8601String(),
        'description': i.description,
        'po_number': i.poNumber,
        'created_at': i.createdAt.toIso8601String(),
      };
}
