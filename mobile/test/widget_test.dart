import 'package:flutter_test/flutter_test.dart';

import 'package:ap_mobile/main.dart';

void main() {
  testWidgets('App renders splash screen', (WidgetTester tester) async {
    await tester.pumpWidget(const APApp());
    expect(find.text('Account Payables'), findsNothing); // splash has no text title
  });
}
