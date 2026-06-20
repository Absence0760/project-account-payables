import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/user.dart';

class AuthStore extends ChangeNotifier {
  static final AuthStore instance = AuthStore._();
  AuthStore._();

  User? _user;
  bool _loading = false;
  String? _error;

  User? get user => _user;
  bool get loading => _loading;
  String? get error => _error;
  bool get loggedIn => _user != null;

  bool get isAdmin => _user?.isAdmin ?? false;
  bool get isManager => _user?.isManager ?? false;
  bool get isCfo => _user?.isCfo ?? false;
  bool get isClerkOnly => _user?.isClerkOnly ?? false;
  bool get canApprove => isAdmin || isManager;
  bool get canViewPayments => isAdmin || isManager || isCfo;
  // Vendor verify/reject + ERP sync — mirrors the backend gate on those
  // routes (require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)). Vendor *reads* are
  // open to CFO too, but the mutating actions are not.
  bool get canManageVendors => isAdmin || isManager;
  // Creating / executing payment runs — backend gate is admin/ap_manager/cfo
  // (same as viewing payments). CFO sign-off on over-threshold runs is a
  // separate server-side gate surfaced via `requires_cfo_approval`.
  bool get canManagePayments => isAdmin || isManager || isCfo;
  // Invoice field editing — mirrors the backend PATCH /api/invoices/{id} gate
  // (admin / ap_manager / cfo). Clerks are read-only here.
  bool get canEditInvoice => isAdmin || isManager || isCfo;

  Future<bool> init() async {
    await ApiClient().init();
    if (!ApiClient().hasToken) return false;
    try {
      _user = await AuthApi.me();
      notifyListeners();
      return true;
    } catch (_) {
      await ApiClient().clearSession();
      return false;
    }
  }

  Future<bool> login(String email, String password, String tenant) async {
    _loading = true;
    _error = null;
    notifyListeners();

    try {
      await ApiClient().setTenant(tenant);
      final token = await AuthApi.login(email, password);
      await ApiClient().setToken(token);
      _user = await AuthApi.me();
      _loading = false;
      notifyListeners();
      return true;
    } catch (e) {
      _loading = false;
      _error = e is ApiException
          ? 'Invalid credentials'
          : 'Connection failed: $e';
      notifyListeners();
      return false;
    }
  }

  Future<void> logout() async {
    await AuthApi.logout();
    _user = null;
    notifyListeners();
  }
}
