import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

import 'package:ap_mobile/config.dart';
import 'package:ap_mobile/services/session.dart';

class ApiException implements Exception {
  final int statusCode;
  final String message;

  ApiException(this.statusCode, this.message);

  @override
  String toString() => 'ApiException($statusCode): $message';
}

class ApiClient {
  static final ApiClient _instance = ApiClient._();
  factory ApiClient() => _instance;
  ApiClient._();

  final _storage = const FlutterSecureStorage();
  http.Client _http = http.Client();

  static const _tokenKey = 'auth_token';
  static const _tenantKey = 'tenant_slug';

  /// Request timeout applied by [get], [post], [getList], [patch] and
  /// [delete] (the JSON/no-body calls — [getBytes]/[postBytes] set their own
  /// longer timeout for file transfers). Overridable only via
  /// [debugConfigure] so tests can shrink it instead of waiting out the real
  /// 10s in a hanging-request test.
  static const _defaultTimeout = Duration(seconds: 10);
  Duration _timeout = _defaultTimeout;

  String? _token;
  String? _tenantSlug;

  // Called on app start to restore session
  Future<void> init() async {
    _token = await _storage.read(key: _tokenKey);
    _tenantSlug = await _storage.read(key: _tenantKey);
    if (_tenantSlug != null) {
      AppConfig.tenantSlug = _tenantSlug;
    }
  }

  bool get hasToken => _token != null;

  /// Test seam: swap the underlying HTTP client for a fake, optionally shrink
  /// the request [timeout] (defaults back to the real 10s when omitted so
  /// tests don't leak a short timeout into each other), and reset the
  /// in-memory session so each test starts from a clean singleton. Not used
  /// by production code — guarded by [visibleForTesting].
  @visibleForTesting
  void debugConfigure({http.Client? client, Duration? timeout}) {
    if (client != null) _http = client;
    _timeout = timeout ?? _defaultTimeout;
    _token = null;
    _tenantSlug = null;
    AppConfig.tenantSlug = null;
  }

  Future<void> setToken(String token) async {
    _token = token;
    await _storage.write(key: _tokenKey, value: token);
  }

  Future<void> setTenant(String slug) async {
    _tenantSlug = slug;
    AppConfig.tenantSlug = slug;
    await _storage.write(key: _tenantKey, value: slug);
  }

  /// End the session everywhere it exists on the device: credentials, the
  /// offline SQLite cache, and the store singletons. This is the single exit
  /// path — explicit logout, a 401 on any request (expired / revoked token),
  /// and a failed session restore all land here — so no forced-logout route
  /// can leave one user's financial data readable by the next one.
  Future<void> clearSession() async {
    _token = null;
    _tenantSlug = null;
    AppConfig.tenantSlug = null;
    await _storage.deleteAll();
    await SessionManager.endSession();
  }

  /// Auth + tenant headers for JSON requests.
  Map<String, String> get _headers {
    final headers = <String, String>{
      'Content-Type': 'application/json',
    };
    if (_token != null) {
      headers['Authorization'] = 'Bearer $_token';
    }
    if (_tenantSlug != null) {
      headers['X-Tenant-Slug'] = _tenantSlug!;
    }
    return headers;
  }

  /// Auth + tenant headers without Content-Type (for multipart uploads).
  Map<String, String> get authHeaders {
    final headers = <String, String>{};
    if (_token != null) {
      headers['Authorization'] = 'Bearer $_token';
    }
    if (_tenantSlug != null) {
      headers['X-Tenant-Slug'] = _tenantSlug!;
    }
    return headers;
  }

  Uri _uri(String path, [Map<String, String>? params]) {
    return Uri.parse('${AppConfig.apiUrl}$path').replace(
      queryParameters: params,
    );
  }

  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? params,
  ]) async {
    final uri = _uri(path, params);
    debugPrint('[API] GET $uri');
    try {
      final response = await _http
          .get(uri, headers: _headers)
          .timeout(_timeout);
      debugPrint('[API] GET $path → ${response.statusCode}');
      return _handleResponse(response);
    } catch (e) {
      debugPrint('[API] GET $path FAILED: $e');
      rethrow;
    }
  }

  Future<List<dynamic>> getList(
    String path, [
    Map<String, String>? params,
  ]) async {
    final response = await _http
        .get(_uri(path, params), headers: _headers)
        .timeout(_timeout);
    return _handleListResponse(response);
  }

  Future<Map<String, dynamic>> post(
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    final uri = _uri(path);
    debugPrint('[API] POST $uri');
    try {
      final response = await _http
          .post(
            uri,
            headers: _headers,
            body: body != null ? jsonEncode(body) : null,
          )
          .timeout(_timeout);
      debugPrint('[API] POST $path → ${response.statusCode}');
      return _handleResponse(response);
    } catch (e) {
      debugPrint('[API] POST $path FAILED: $e');
      rethrow;
    }
  }

  Future<Map<String, dynamic>> patch(
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    final response = await _http
        .patch(
          _uri(path),
          headers: _headers,
          body: body != null ? jsonEncode(body) : null,
        )
        .timeout(_timeout);
    return _handleResponse(response);
  }

  /// Fetch a file's raw bytes (auth + tenant headers attached) via the
  /// swappable HTTP client. [path] is API-relative (e.g.
  /// `/api/invoices/file/{key}`). Used by the invoice file viewer to load a PDF
  /// the native engine can't fetch with custom headers. A 401 clears the
  /// session; any other non-2xx throws an [ApiException].
  Future<Uint8List> getBytes(String path) async {
    final uri = Uri.parse('${AppConfig.apiBaseUrl}$path');
    final response = await _http
        .get(uri, headers: authHeaders)
        .timeout(const Duration(seconds: 30));
    if (response.statusCode == 401) {
      await clearSession();
      throw ApiException(401, 'Unauthorized');
    }
    if (response.statusCode >= 400) {
      throw ApiException(response.statusCode, 'Failed to load file');
    }
    return response.bodyBytes;
  }

  /// POST [body] (JSON) and return the raw response bytes (auth + tenant
  /// headers attached) via the swappable HTTP client. [path] is API-relative
  /// (e.g. `/invoices/bulk/export`). Used by the bulk-export action, whose
  /// endpoint streams a CSV/XML file rather than JSON. A 401 clears the
  /// session; any other non-2xx throws an [ApiException]. Returns the body
  /// bytes plus the server-suggested filename parsed from `Content-Disposition`
  /// (null when absent).
  Future<({Uint8List bytes, String? filename})> postBytes(
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    final uri = _uri(path);
    debugPrint('[API] POST(bytes) $uri');
    final response = await _http
        .post(
          uri,
          headers: _headers,
          body: body != null ? jsonEncode(body) : null,
        )
        .timeout(const Duration(seconds: 30));
    debugPrint('[API] POST(bytes) $path → ${response.statusCode}');
    if (response.statusCode == 401) {
      await clearSession();
      throw ApiException(401, 'Unauthorized');
    }
    if (response.statusCode >= 400) {
      throw ApiException(response.statusCode, response.body);
    }
    return (
      bytes: response.bodyBytes,
      filename: _filenameFromDisposition(response.headers['content-disposition']),
    );
  }

  /// Pull a `filename="..."` token out of a `Content-Disposition` header.
  /// Returns null when the header is absent or has no filename.
  static String? _filenameFromDisposition(String? disposition) {
    if (disposition == null) return null;
    final match = RegExp(r'filename\*?=(?:UTF-8'
            "''"
            r')?"?([^";]+)"?')
        .firstMatch(disposition);
    return match?.group(1)?.trim();
  }

  Future<void> delete(String path) async {
    final response = await _http
        .delete(_uri(path), headers: _headers)
        .timeout(_timeout);
    // Same forced-logout treatment as every other verb — clearSession()'s
    // "a 401 on any request lands here" is only true if this path honours it.
    if (response.statusCode == 401) {
      await clearSession();
      throw ApiException(401, 'Unauthorized');
    }
    if (response.statusCode >= 400) {
      throw ApiException(response.statusCode, response.body);
    }
  }

  /// Forced logout on a 401 is **awaited before the throw** so the local
  /// teardown (cache + stores) has finished by the time a caller's `catch`
  /// runs — otherwise an offline-fallback `catch` could still read the cache
  /// of the session being torn down.
  Future<Map<String, dynamic>> _handleResponse(http.Response response) async {
    if (response.statusCode == 401) {
      await clearSession();
      throw ApiException(401, 'Unauthorized');
    }
    if (response.statusCode >= 400) {
      throw ApiException(response.statusCode, response.body);
    }
    if (response.body.isEmpty) return {};
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<List<dynamic>> _handleListResponse(http.Response response) async {
    if (response.statusCode == 401) {
      await clearSession();
      throw ApiException(401, 'Unauthorized');
    }
    if (response.statusCode >= 400) {
      throw ApiException(response.statusCode, response.body);
    }
    if (response.body.isEmpty) return [];
    final decoded = jsonDecode(response.body);
    if (decoded is List) return decoded;
    // Backend wraps lists in { "items": [...] } or { "invoices": [...] }
    if (decoded is Map<String, dynamic>) {
      for (final value in decoded.values) {
        if (value is List) return value;
      }
    }
    return [];
  }
}
