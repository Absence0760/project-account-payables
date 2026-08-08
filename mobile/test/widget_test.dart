import 'package:flutter_test/flutter_test.dart';

import 'package:feohledger_mobile/main.dart';
import 'package:feohledger_mobile/services/push_service.dart';

void main() {
  testWidgets('App renders splash screen', (WidgetTester tester) async {
    await tester.pumpWidget(const APApp());
    expect(find.text('FeohLedger'), findsNothing); // splash has no text title
  });

  testWidgets('MaterialApp is wired to PushService.navigatorKey', (tester) async {
    // PushService._handleMessageTap runs outside the widget tree (a
    // top-level FCM callback with no BuildContext of its own), so it needs
    // this key to navigate on a notification tap. Proves the wiring without
    // touching Firebase (which isn't configured in tests).
    await tester.pumpWidget(const APApp());
    expect(PushService.navigatorKey.currentState, isNotNull);
  });
}
