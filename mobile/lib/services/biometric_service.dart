import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:local_auth/local_auth.dart';

/// Biometric authentication — Face ID, fingerprint, or device PIN.
class BiometricService {
  static final BiometricService instance = BiometricService._();
  BiometricService._();

  final _auth = LocalAuthentication();
  final _storage = const FlutterSecureStorage();

  static const _enabledKey = 'biometric_enabled';

  /// Check if the device supports biometrics.
  Future<bool> get isAvailable async {
    try {
      final canCheck = await _auth.canCheckBiometrics;
      final isSupported = await _auth.isDeviceSupported();
      return canCheck || isSupported;
    } on Exception {
      // PlatformException on a real device, MissingPluginException when no
      // native impl is registered (e.g. unsupported platform / test VM). The
      // contract is "is it available?" — any failure means no.
      return false;
    }
  }

  /// Check if the user has opted into biometric login.
  Future<bool> get isEnabled async {
    final val = await _storage.read(key: _enabledKey);
    return val == 'true';
  }

  /// Enable or disable biometric login.
  Future<void> setEnabled(bool enabled) async {
    await _storage.write(key: _enabledKey, value: enabled.toString());
  }

  /// Prompt the user to authenticate with biometrics.
  /// Returns true if authenticated successfully.
  Future<bool> authenticate() async {
    try {
      return await _auth.authenticate(
        localizedReason: 'Unlock FeohLedger',
      );
    } on Exception catch (e) {
      // PlatformException on a real device, MissingPluginException when no
      // native impl is registered — either way, treat as auth failure.
      debugPrint('[biometric] Auth failed: $e');
      return false;
    }
  }
}
