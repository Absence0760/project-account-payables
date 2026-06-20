import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/audit_entry.dart';
import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/services/offline_store.dart';

/// Immutable bundle of the advanced-search filters (vendor, PO, amount range,
/// due-date range). Held alongside the quick status-chip filter + search box.
/// `isEmpty` lets the UI show an "advanced filters active" indicator.
class InvoiceSearchFilters {
  final String? vendor;
  final String? poNumber;
  final double? amountMin;
  final double? amountMax;
  final DateTime? dueDateFrom;
  final DateTime? dueDateTo;

  const InvoiceSearchFilters({
    this.vendor,
    this.poNumber,
    this.amountMin,
    this.amountMax,
    this.dueDateFrom,
    this.dueDateTo,
  });

  static const empty = InvoiceSearchFilters();

  bool get isEmpty =>
      (vendor == null || vendor!.isEmpty) &&
      (poNumber == null || poNumber!.isEmpty) &&
      amountMin == null &&
      amountMax == null &&
      dueDateFrom == null &&
      dueDateTo == null;

  int get activeCount => [
        vendor != null && vendor!.isNotEmpty,
        poNumber != null && poNumber!.isNotEmpty,
        amountMin != null,
        amountMax != null,
        dueDateFrom != null,
        dueDateTo != null,
      ].where((e) => e).length;
}

class InvoiceStore extends ChangeNotifier {
  static final InvoiceStore instance = InvoiceStore._();
  InvoiceStore._();

  List<Invoice> _invoices = [];
  bool _loading = false;
  String? _error;
  String? _statusFilter;
  String? _searchQuery;
  InvoiceSearchFilters _filters = InvoiceSearchFilters.empty;
  bool _fromCache = false;

  List<Invoice> get invoices => _invoices;
  bool get loading => _loading;
  String? get error => _error;
  String? get statusFilter => _statusFilter;
  InvoiceSearchFilters get filters => _filters;
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
    _filters = InvoiceSearchFilters.empty;
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

  /// Apply the advanced-search filters (vendor / PO / amount range / due-date
  /// range) and refetch. Pass [InvoiceSearchFilters.empty] to clear them.
  void setFilters(InvoiceSearchFilters filters) {
    _filters = filters;
    fetch();
  }

  Future<void> fetch() async {
    _loading = true;
    _error = null;
    notifyListeners();

    final f = _filters;
    final cacheKey = 'invoices_${_statusFilter ?? 'all'}_${_searchQuery ?? ''}'
        '_${f.vendor ?? ''}_${f.poNumber ?? ''}_${f.amountMin ?? ''}'
        '_${f.amountMax ?? ''}_${f.dueDateFrom?.toIso8601String() ?? ''}'
        '_${f.dueDateTo?.toIso8601String() ?? ''}';

    try {
      _invoices = await InvoiceApi.list(
        status: _statusFilter,
        search: _searchQuery,
        vendor: f.vendor,
        poNumber: f.poNumber,
        amountMin: f.amountMin,
        amountMax: f.amountMax,
        dueDateFrom: f.dueDateFrom,
        dueDateTo: f.dueDateTo,
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

  /// Edit invoice fields. [changes] is the partial PATCH body (money values as
  /// string-Decimal). On success returns the updated [Invoice] and refreshes
  /// the list so the edited row reflects the change; on failure records the
  /// error and returns null. Not a money-moving write — no idempotency key
  /// needed (a repeated PATCH is naturally idempotent: it sets the same fields).
  Future<Invoice?> update(String id, Map<String, dynamic> changes) async {
    try {
      final updated = await InvoiceApi.update(id, changes);
      await fetch();
      return updated;
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return null;
    }
  }

  /// Fetch the invoice's activity timeline (audit log). Read-only; the screen
  /// owns its own loading/error UI, so this just returns the entries (oldest
  /// first) or rethrows for the caller to surface.
  Future<List<AuditEntry>> fetchAuditLog(String id) {
    return InvoiceApi.auditLog(id);
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
        'gl_account': i.glAccount,
        'created_at': i.createdAt.toIso8601String(),
      };
}
