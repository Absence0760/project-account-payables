import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/invoice.dart';

class InvoiceStore extends ChangeNotifier {
  static final InvoiceStore instance = InvoiceStore._();
  InvoiceStore._();

  List<Invoice> _invoices = [];
  bool _loading = false;
  String? _error;
  String? _statusFilter;
  String? _searchQuery;

  List<Invoice> get invoices => _invoices;
  bool get loading => _loading;
  String? get error => _error;
  String? get statusFilter => _statusFilter;

  List<Invoice> get pendingApproval => _invoices
      .where((i) => i.status == InvoiceStatus.readyForReview)
      .toList();

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

    try {
      _invoices = await InvoiceApi.list(
        status: _statusFilter,
        search: _searchQuery,
      );
      _loading = false;
      notifyListeners();
    } catch (e) {
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
}
