import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/models/audit_entry.dart';
import 'package:ap_mobile/models/contract.dart';
import 'package:ap_mobile/models/exception.dart';
import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/models/payment.dart';
import 'package:ap_mobile/models/user.dart';

class AuthApi {
  static final _api = ApiClient();

  static Future<String> login(String email, String password) async {
    final data = await _api.post('/auth/login', {
      'email': email,
      'password': password,
    });
    return data['access_token'] as String;
  }

  static Future<User> me() async {
    final data = await _api.get('/auth/me');
    return User.fromJson(data);
  }

  static Future<void> logout() async {
    try {
      await _api.post('/auth/logout');
    } catch (_) {
      // Ignore — clear local session regardless
    }
    await _api.clearSession();
  }
}

class InvoiceApi {
  static final _api = ApiClient();

  static Future<List<Invoice>> list({
    String? status,
    String? search,
    int page = 1,
    int perPage = 20,
  }) async {
    final params = <String, String>{
      'page': page.toString(),
      'per_page': perPage.toString(),
    };
    if (status != null) params['status'] = status;
    if (search != null) params['search'] = search;

    final items = await _api.getList('/invoices', params);
    return items
        .map((e) => Invoice.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<Invoice> getById(String id) async {
    final data = await _api.get('/invoices/$id');
    return Invoice.fromJson(data);
  }

  static Future<Invoice> approve(String id) async {
    final data = await _api.post('/invoices/$id/approve');
    return Invoice.fromJson(data);
  }

  static Future<Invoice> reject(String id, String reason) async {
    final data = await _api.post('/invoices/$id/reject', {
      'reason': reason,
    });
    return Invoice.fromJson(data);
  }

  /// Edit invoice fields via `PATCH /api/invoices/{id}` (admin/ap_manager/cfo;
  /// 409 if the invoice is in an immutable status). [changes] is the partial
  /// body — only the keys present are updated. Money fields MUST be passed as
  /// string-Decimal (never a lossy float); the backend's Pydantic `Decimal`
  /// parses the string exactly. The backend maps `vendor` → `vendor_name`.
  static Future<Invoice> update(String id, Map<String, dynamic> changes) async {
    final data = await _api.patch('/invoices/$id', changes);
    return Invoice.fromJson(data);
  }

  /// Per-invoice activity timeline (audit log) via
  /// `GET /api/invoices/{id}/audit-log` — the operational trail, open to any
  /// authenticated user. Returns a bare JSON array, oldest-first.
  static Future<List<AuditEntry>> auditLog(String id) async {
    final items = await _api.getList('/invoices/$id/audit-log');
    return items
        .map((e) => AuditEntry.fromJson(e as Map<String, dynamic>))
        .toList();
  }
}

class ContractApi {
  static final _api = ApiClient();

  static Future<List<Contract>> list({
    String? status,
    String? contractType,
    String? search,
    int page = 1,
    int pageSize = 20,
  }) async {
    final params = <String, String>{
      'page': page.toString(),
      'page_size': pageSize.toString(),
    };
    if (status != null) params['status'] = status;
    if (contractType != null) params['contract_type'] = contractType;
    if (search != null) params['search'] = search;

    final items = await _api.getList('/contracts', params);
    return items
        .map((e) => Contract.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<Contract> getById(String id) async {
    final data = await _api.get('/contracts/$id');
    return Contract.fromJson(data);
  }

  static Future<Contract> activate(String id) async {
    final data = await _api.post('/contracts/$id/activate');
    return Contract.fromJson(data);
  }

  static Future<Contract> terminate(String id) async {
    final data = await _api.post('/contracts/$id/terminate');
    return Contract.fromJson(data);
  }

  static Future<Contract> cancel(String id) async {
    final data = await _api.post('/contracts/$id/cancel');
    return Contract.fromJson(data);
  }
}

class ExceptionApi {
  static final _api = ApiClient();

  static Future<List<ApException>> list({
    String? status,
    int page = 1,
    int perPage = 20,
  }) async {
    final params = <String, String>{
      'page': page.toString(),
      'per_page': perPage.toString(),
    };
    if (status != null) params['status'] = status;

    final items = await _api.getList('/exceptions', params);
    return items
        .map((e) => ApException.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Resolve / escalate / dismiss an exception. The backend route is
  /// `POST /exceptions/{id}/resolve` with `{action, resolution}` (action is one
  /// of resolve | escalate | dismiss); a missing resolution is rejected.
  static Future<void> act(
    String id, {
    required String action,
    required String resolution,
  }) async {
    await _api.post('/exceptions/$id/resolve', {
      'action': action,
      'resolution': resolution,
    });
  }
}

class DashboardApi {
  static final _api = ApiClient();

  static Future<DashboardData> get() async {
    final data = await _api.get('/dashboard');
    return DashboardData.fromJson(data);
  }
}

class PaymentApi {
  static final _api = ApiClient();

  static Future<List<Payment>> list({
    String? status,
    int page = 1,
    int perPage = 20,
  }) async {
    final params = <String, String>{
      'page': page.toString(),
      'per_page': perPage.toString(),
    };
    if (status != null) params['status'] = status;

    final items = await _api.getList('/payments', params);
    return items
        .map((e) => Payment.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<Map<String, dynamic>> summary() async {
    return ApiClient().get('/payments/summary');
  }
}
