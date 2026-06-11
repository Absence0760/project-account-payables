import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/widgets/status_badge.dart';

Widget _host(Widget child) => MaterialApp(home: Scaffold(body: child));

void main() {
  testWidgets('renders the status label text', (tester) async {
    await tester.pumpWidget(_host(
      const StatusBadge(status: InvoiceStatus.readyForReview),
    ));
    expect(find.text('Ready for Review'), findsOneWidget);
  });

  testWidgets('renders a label for every status without throwing',
      (tester) async {
    for (final status in InvoiceStatus.values) {
      await tester.pumpWidget(_host(StatusBadge(status: status)));
      expect(find.text(status.label), findsOneWidget,
          reason: 'missing label for ${status.value}');
    }
  });

  testWidgets('tints the text by status color (approved = green)',
      (tester) async {
    await tester.pumpWidget(_host(
      const StatusBadge(status: InvoiceStatus.approved),
    ));
    final text = tester.widget<Text>(find.text('Approved'));
    expect(text.style?.color, Colors.green);
  });
}
