import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/config.dart';
import 'package:ap_mobile/models/mfa_challenge.dart';
import 'package:ap_mobile/models/user.dart';
import 'package:ap_mobile/services/session.dart';

/// Outcome of [AuthStore.login] / [AuthStore.completeMfa].
enum LoginOutcome {
  /// Fully signed in — a real access token is stored and the user is loaded.
  success,

  /// Password accepted but a second factor is required. [LoginResult.challenge]
  /// carries the challenge token + offered methods; the UI routes to the MFA
  /// code-entry screen.
  mfaRequired,

  /// Login failed (bad credentials, transport error, etc.). [AuthStore.error]
  /// holds the user-facing message.
  failure,
}

/// The result of a login attempt. [challenge] is non-null only when
/// [outcome] is [LoginOutcome.mfaRequired].
class LoginResult {
  final LoginOutcome outcome;
  final MFAChallenge? challenge;

  const LoginResult(this.outcome, [this.challenge]);

  bool get isSuccess => outcome == LoginOutcome.success;
}

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
  // Bulk invoice operations (delete / status change) — mirrors the backend
  // gate on POST /api/invoices/bulk/{delete,status}
  // (require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)). Clerks excluded.
  bool get canBulkEditInvoices => isAdmin || isManager || isCfo;
  // Admin surfaces — user management + organization settings. Both backend
  // surfaces (/api/admin/*, PATCH /api/organization) are admin-only.
  bool get isOrgAdmin => isAdmin;
  // Predictive cash-flow forecast — mirrors the backend analytics gate
  // (_CFO_ROLES = admin, cfo on /analytics/cashflow_forecast + /cash_position).
  // Note: ap_manager is deliberately excluded (it's a privileged CFO surface).
  bool get canViewCashFlow => isAdmin || isCfo;
  // Workflow management (read-only on mobile) — mirrors the web nav
  // `roles: ['admin']`. The GET /api/workflows reads are open to any authed
  // user, so this is a UI gate matching the desktop entry point, not a security
  // boundary.
  bool get canViewWorkflows => isAdmin;

  /// Drop all in-memory state. Called on logout / forced logout through
  /// `SessionManager.endSession` — this is a process-lifetime singleton, so
  /// without this a signed-out user would still be `loggedIn` for the next
  /// account on the device. Tests use it to decouple from run order.
  void reset() {
    _user = null;
    _loading = false;
    _error = null;
    notifyListeners();
  }

  Future<bool> init() async {
    await ApiClient().init();
    if (!ApiClient().hasToken) return false;
    try {
      await _loadUser();
      notifyListeners();
      return true;
    } catch (_) {
      await ApiClient().clearSession();
      return false;
    }
  }

  /// Password login. On a clean login this stores the JWT and loads the user
  /// ([LoginOutcome.success]). When the backend returns an MFA challenge
  /// instead of a token, NO token is stored and the result carries the
  /// [MFAChallenge] so the UI can route to the code-entry screen
  /// ([LoginOutcome.mfaRequired]). Errors set [error] and return
  /// [LoginOutcome.failure].
  Future<LoginResult> login(String email, String password, String tenant) async {
    _loading = true;
    _error = null;
    notifyListeners();

    try {
      await ApiClient().setTenant(tenant);
      final data = await AuthApi.login(email, password);

      // MFA challenge — the password was accepted but a second factor is
      // required. Don't store a token; hand the challenge back to the UI.
      if (MFAChallenge.isChallenge(data)) {
        _loading = false;
        notifyListeners();
        return LoginResult(
          LoginOutcome.mfaRequired,
          MFAChallenge.fromJson(data),
        );
      }

      await _finishAuth(data['access_token'] as String);
      _loading = false;
      notifyListeners();
      return const LoginResult(LoginOutcome.success);
    } catch (e) {
      _loading = false;
      _error = e is ApiException
          ? 'Invalid credentials'
          : 'Connection failed: $e';
      notifyListeners();
      return const LoginResult(LoginOutcome.failure);
    }
  }

  /// Complete the MFA step: trade the challenge token + a code for the real
  /// access token via `POST /api/auth/mfa/verify`, then load the user — exactly
  /// the same token-storage tail as the no-MFA login path. [method] is `totp`
  /// or `email`. A wrong / expired code surfaces a friendly [error] and returns
  /// [LoginOutcome.failure] (the UI keeps the user on the code screen to retry).
  Future<LoginResult> completeMfa({
    required String challengeToken,
    required String code,
    required String method,
  }) async {
    _loading = true;
    _error = null;
    notifyListeners();

    try {
      final token = await AuthApi.verifyMfa(
        challengeToken: challengeToken,
        code: code,
        method: method,
      );
      await _finishAuth(token);
      _loading = false;
      notifyListeners();
      return const LoginResult(LoginOutcome.success);
    } catch (e) {
      _loading = false;
      // 401 = wrong/expired code or a stale challenge. Anything else is a
      // transport problem. Neither leaks why (no enumeration).
      _error = e is ApiException
          ? (e.statusCode == 401
              ? 'Invalid or expired code. Please try again.'
              : 'Could not verify the code. Please try again.')
          : 'Connection failed: $e';
      notifyListeners();
      return const LoginResult(LoginOutcome.failure);
    }
  }

  /// Ask the backend to email a one-time code (email-OTP backup factor).
  /// Returns true on success; on failure sets [error] and returns false so the
  /// code screen can show a retry affordance.
  Future<bool> requestEmailOtp(String challengeToken) async {
    _error = null;
    try {
      await AuthApi.requestEmailOtp(challengeToken);
      return true;
    } catch (e) {
      _error = e is ApiException
          ? 'Could not send the email code. Please try again.'
          : 'Connection failed: $e';
      notifyListeners();
      return false;
    }
  }

  /// Shared token-storage tail for both the no-MFA login and the post-MFA
  /// verify paths: persist the JWT to secure storage and load the profile.
  Future<void> _finishAuth(String token) async {
    await ApiClient().setToken(token);
    await _loadUser();
  }

  /// Load the profile and bind the device's local state (offline cache + store
  /// singletons) to this `(tenant, user)` BEFORE publishing the user — a
  /// different session than the cache last saw purges it, and that purge also
  /// resets the stores. Assigning [_user] afterwards keeps it from being
  /// cleared by its own sign-in.
  Future<void> _loadUser() async {
    final user = await AuthApi.me();
    final tenantSlug = AppConfig.tenantSlug;
    if (tenantSlug == null || tenantSlug.isEmpty) {
      // Tenant unknown (shouldn't happen — login sets it, restore reads it
      // back). Rather than mint a scope every tenant with this user would
      // share, leave the cache torn down and inert: the app still works
      // online, it just can't cache.
      await SessionManager.endSession();
    } else {
      await SessionManager.beginSession(
        tenantSlug: tenantSlug,
        userId: user.id,
      );
    }
    _user = user;
  }

  /// Sign out. The local teardown (offline cache + every store singleton) runs
  /// inside `ApiClient.clearSession()`, which `AuthApi.logout` calls — the same
  /// chokepoint a 401-forced logout goes through.
  Future<void> logout() async {
    await AuthApi.logout();
    notifyListeners();
  }
}
