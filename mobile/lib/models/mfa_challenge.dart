/// The `MFAChallengeResponse` the backend returns from `POST /api/auth/login`
/// when the password checks out but the user still has to clear a second
/// factor (`FEOH_MFA_ENABLED=true` and the user is enrolled, or the org enforces
/// MFA). The client trades [challengeToken] + a code for the real access token
/// at `POST /api/auth/mfa/verify`.
///
/// Mirrors `backend/app/schemas/auth.py::MFAChallengeResponse`.
class MFAChallenge {
  /// Short-lived JWT (`typ=mfa_challenge`) that proves the password was
  /// accepted. Re-sent on every verify / email-OTP request.
  final String challengeToken;

  /// Offered factors, e.g. `["totp", "email"]`. The mobile app handles the
  /// `totp` and `email` methods; `passkey` (WebAuthn) is web-only for now and
  /// is filtered out by [supportsTotp] / [supportsEmail] consumers.
  final List<String> methods;

  /// True when the org enforces MFA but the user hasn't enrolled a factor yet.
  /// Enrollment is web/desktop-only today, so the mobile app surfaces a message
  /// pointing the user to the web app rather than a half-working enroll flow.
  final bool mustEnroll;

  const MFAChallenge({
    required this.challengeToken,
    required this.methods,
    required this.mustEnroll,
  });

  /// Whether the login response is an MFA challenge (vs a `TokenResponse`).
  /// The backend sets `mfa_required: true` only on the challenge shape.
  static bool isChallenge(Map<String, dynamic> json) =>
      json['mfa_required'] == true;

  factory MFAChallenge.fromJson(Map<String, dynamic> json) {
    return MFAChallenge(
      challengeToken: json['mfa_challenge_token'] as String,
      methods: (json['methods'] as List<dynamic>? ?? const [])
          .map((e) => e.toString())
          .toList(),
      mustEnroll: json['must_enroll'] as bool? ?? false,
    );
  }

  bool get supportsTotp => methods.contains('totp');
  bool get supportsEmail => methods.contains('email');
}
