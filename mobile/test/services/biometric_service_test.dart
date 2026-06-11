import 'package:flutter_test/flutter_test.dart';

import 'package:ap_mobile/services/biometric_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // With no native local_auth plugin registered (the test VM, and any
  // unsupported platform), the method channel throws MissingPluginException —
  // which is NOT a PlatformException. isAvailable must still resolve to false
  // rather than letting that exception escape its "is it available?" contract.
  test('isAvailable returns false when the plugin is unavailable', () async {
    await expectLater(BiometricService.instance.isAvailable, completion(isFalse));
  });

  test('authenticate returns false when the plugin is unavailable', () async {
    await expectLater(
      BiometricService.instance.authenticate(),
      completion(isFalse),
    );
  });
}
