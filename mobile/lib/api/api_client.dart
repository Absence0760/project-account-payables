import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

import 'package:ap_mobile/config.dart';

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
  final _http = http.Client();

  static const _tokenKey = 'auth_token';
  static const _tenantKey = 'tenant_slug';

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

  Future<void> setToken(String token) async {
    _token = token;
    await _storage.write(key: _tokenKey, value: token);
  }

  Future<void> setTenant(String slug) async {
    _tenantSlug = slug;
    AppConfig.tenantSlug = slug;
    await _storage.write(key: _tenantKey, value: slug);
  }

  Future<void> clearSession() async {
    _token = null;
    _tenantSlug = null;
    AppConfig.tenantSlug = null;
    await _storage.deleteAll();
  }

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

  Uri _uri(String path, [Map<String, String>? params]) {
    return Uri.parse('${AppConfig.apiUrl}$path').replace(
      queryParameters: params,
    );
  }

  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? params,
  ]) async {
    final response = await _http.get(_uri(path, params), headers: _headers);
    return _handleResponse(response);
  }

  Future<List<dynamic>> getList(
    String path, [
    Map<String, String>? params,
  ]) async {
    final response = await _http.get(_uri(path, params), headers: _headers);
    return _handleListResponse(response);
  }

  Future<Map<String, dynamic>> post(
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    final response = await _http.post(
      _uri(path),
      headers: _headers,
      body: body != null ? jsonEncode(body) : null,
    );
    return _handleResponse(response);
  }

  Future<Map<String, dynamic>> patch(
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    final response = await _http.patch(
      _uri(path),
      headers: _headers,
      body: body != null ? jsonEncode(body) : null,
    );
    return _handleResponse(response);
  }

  Future<void> delete(String path) async {
    final response = await _http.delete(_uri(path), headers: _headers);
    if (response.statusCode >= 400) {
      throw ApiException(response.statusCode, response.body);
    }
  }

  Map<String, dynamic> _handleResponse(http.Response response) {
    if (response.statusCode == 401) {
      clearSession();
      throw ApiException(401, 'Unauthorized');
    }
    if (response.statusCode >= 400) {
      throw ApiException(response.statusCode, response.body);
    }
    if (response.body.isEmpty) return {};
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  List<dynamic> _handleListResponse(http.Response response) {
    if (response.statusCode == 401) {
      clearSession();
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
