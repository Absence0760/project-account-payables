import 'package:ap_mobile/api/api_client.dart';
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
