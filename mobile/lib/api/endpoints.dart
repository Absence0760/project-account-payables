import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/models/audit_entry.dart';
import 'package:ap_mobile/models/contract.dart';
import 'package:ap_mobile/models/exception.dart';
import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/models/notification.dart';
import 'package:ap_mobile/models/payment.dart';
import 'package:ap_mobile/models/payment_queue.dart';
import 'package:ap_mobile/models/user.dart';
import 'package:ap_mobile/models/vendor.dart';

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
    String? vendor,
    String? poNumber,
    double? amountMin,
    double? amountMax,
    DateTime? dueDateFrom,
    DateTime? dueDateTo,
    int page = 1,
    int perPage = 20,
  }) async {
    final params = <String, String>{
      'page': page.toString(),
      'per_page': perPage.toString(),
    };
    if (status != null) params['status'] = status;
    if (search != null) params['search'] = search;
    // Advanced-search filters — backend query-param names in
    // `backend/app/api/invoices.py::list_invoices`.
    if (vendor != null && vendor.isNotEmpty) params['vendor'] = vendor;
    if (poNumber != null && poNumber.isNotEmpty) {
      params['po_number'] = poNumber;
    }
    if (amountMin != null) params['amount_min'] = amountMin.toString();
    if (amountMax != null) params['amount_max'] = amountMax.toString();
    if (dueDateFrom != null) {
      params['due_date_from'] = _isoDate(dueDateFrom);
    }
    if (dueDateTo != null) params['due_date_to'] = _isoDate(dueDateTo);

    final items = await _api.getList('/invoices', params);
    return items
        .map((e) => Invoice.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// `YYYY-MM-DD` — the backend parses these into `date` (FastAPI `date`
  /// query params).
  static String _isoDate(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';

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

/// One page of the notification list plus the user's total unread count.
/// The backend returns `{items, total, unread, page, page_size}`; `unread` is
/// the user's total unread count regardless of the page window, so the same
/// response feeds both the list and the app-bar badge.
class NotificationPage {
  final List<AppNotification> items;
  final int total;
  final int unread;

  NotificationPage({
    required this.items,
    required this.total,
    required this.unread,
  });
}

class NotificationApi {
  static final _api = ApiClient();

  /// `GET /api/notifications` — the current user's notifications, newest first.
  /// `unreadOnly` filters to unread rows; `unread` in the result is always the
  /// total unread count.
  static Future<NotificationPage> list({
    bool unreadOnly = false,
    int page = 1,
    int pageSize = 20,
  }) async {
    final params = <String, String>{
      'page': page.toString(),
      'page_size': pageSize.toString(),
    };
    if (unreadOnly) params['unread_only'] = 'true';

    final data = await _api.get('/notifications', params);
    final items = (data['items'] as List? ?? [])
        .map((e) => AppNotification.fromJson(e as Map<String, dynamic>))
        .toList();
    return NotificationPage(
      items: items,
      total: (data['total'] as num?)?.toInt() ?? items.length,
      unread: (data['unread'] as num?)?.toInt() ?? 0,
    );
  }

  /// `GET /api/notifications/unread-count` — cheap badge count.
  static Future<int> unreadCount() async {
    final data = await _api.get('/notifications/unread-count');
    return (data['unread'] as num?)?.toInt() ?? 0;
  }

  /// `POST /api/notifications/{id}/read` — mark one read (idempotent server-side).
  static Future<void> markRead(String id) async {
    await _api.post('/notifications/$id/read');
  }

  /// `POST /api/notifications/read-all` — mark every unread row read; returns
  /// the number updated.
  static Future<int> markAllRead() async {
    final data = await _api.post('/notifications/read-all');
    return (data['updated'] as num?)?.toInt() ?? 0;
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

  static Future<PaymentSummary> summary() async {
    final data = await _api.get('/payments/summary');
    return PaymentSummary.fromJson(data);
  }

  /// Approved invoices awaiting payment — `GET /api/payments/queue`.
  /// Response is `{items, total, total_amount, total_savings}`; `getList`
  /// unwraps the `items` array.
  static Future<List<PaymentQueueItem>> queue() async {
    final items = await _api.getList('/payments/queue');
    return items
        .map((e) => PaymentQueueItem.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Payment runs (batches) — `GET /api/payments/runs/` (the trailing slash
  /// matters; the backend route is declared that way). `{items, total, ...}`.
  static Future<List<PaymentRun>> runs({String? status}) async {
    final params = <String, String>{};
    if (status != null) params['status'] = status;
    final items = await _api.getList('/payments/runs/', params);
    return items
        .map((e) => PaymentRun.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Create a draft payment run from selected invoices + per-row method.
  /// `POST /api/payments/runs`. Returns the raw `{id, status, ...}` payload
  /// (includes `requires_cfo_approval` + a human message).
  static Future<Map<String, dynamic>> createRun(
    List<PaymentRunSelection> selections,
  ) async {
    return _api.post('/payments/runs', {
      'items': selections.map((s) => s.toJson()).toList(),
    });
  }

  /// Execute a draft run via the configured payment adapter.
  /// `POST /api/payments/runs/{id}/execute`.
  static Future<Map<String, dynamic>> executeRun(String id) async {
    return _api.post('/payments/runs/$id/execute');
  }

  /// Cancel a draft run (releases the invoices back to the queue).
  /// `POST /api/payments/runs/{id}/cancel`.
  static Future<Map<String, dynamic>> cancelRun(String id) async {
    return _api.post('/payments/runs/$id/cancel');
  }
}

class VendorApi {
  static final _api = ApiClient();

  static Future<List<Vendor>> list({
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

    final items = await _api.getList('/vendors', params);
    return items
        .map((e) => Vendor.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Verify an unverified vendor (admin / ap_manager). 409 if not unverified.
  static Future<Vendor> verify(String id) async {
    final data = await _api.post('/vendors/$id/verify');
    return Vendor.fromJson(data);
  }

  /// Reject an unverified vendor (admin / ap_manager).
  static Future<Vendor> reject(String id) async {
    final data = await _api.post('/vendors/$id/reject');
    return Vendor.fromJson(data);
  }

  /// Pull vendors from the connected ERP (admin / ap_manager). Returns the
  /// `{success, message, created, updated, unchanged}` summary; 400 when no
  /// ERP is configured.
  static Future<Map<String, dynamic>> syncErp() async {
    return _api.post('/vendors/sync-erp');
  }
}
