class AppConfig {
  static const String defaultApiUrl = 'http://localhost:8000';
  static const String apiPrefix = '/api';

  // Set via login screen or env
  static String apiBaseUrl = defaultApiUrl;
  static String? tenantSlug;

  static String get apiUrl => '$apiBaseUrl$apiPrefix';
}
