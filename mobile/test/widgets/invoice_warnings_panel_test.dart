import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/widgets/invoice_warnings_panel.dart';

Widget _host(Widget child) =>
    MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child)));

void main() {
  testWidgets('renders nothing when there are no warnings and no PO match',
      (tester) async {
    await tester.pumpWidget(_host(
      const InvoiceWarningsPanel(warnings: [], poMatch: null),
    ));
    expect(find.byType(SizedBox), findsWidgets); // the shrink placeholder
    expect(find.text('Warnings & fraud flags'), findsNothing);
    expect(find.text('PO Match'), findsNothing);
  });

  testWidgets('renders nothing when the only PO match is no_po', (tester) async {
    await tester.pumpWidget(_host(
      const InvoiceWarningsPanel(
        warnings: [],
        poMatch: PoMatch(matchType: 'none', status: 'no_po'),
      ),
    ));
    expect(find.text('PO Match'), findsNothing);
  });

  testWidgets('lists each warning message under the section header',
      (tester) async {
    await tester.pumpWidget(_host(
      const InvoiceWarningsPanel(
        warnings: [
          InvoiceWarning(
            type: 'duplicate',
            severity: WarningSeverity.warning,
            message: 'Duplicate invoice number for this vendor',
          ),
          InvoiceWarning(
            type: 'missing_field',
            severity: WarningSeverity.error,
            message: 'Missing vendor name',
          ),
        ],
        poMatch: null,
      ),
    ));

    expect(find.text('Warnings & fraud flags'), findsOneWidget);
    expect(find.text('Duplicate invoice number for this vendor'),
        findsOneWidget);
    expect(find.text('Missing vendor name'), findsOneWidget);
  });

  testWidgets('renders the PO match panel with variance and issues',
      (tester) async {
    await tester.pumpWidget(_host(
      const InvoiceWarningsPanel(
        warnings: [],
        poMatch: PoMatch(
          matchType: '3-way',
          status: 'mismatch',
          variancePct: 12.5,
          withinTolerance: false,
          issues: ['Amount variance of 12.5%'],
        ),
      ),
    ));

    expect(find.text('PO Match'), findsOneWidget);
    expect(find.text('3-way match'), findsOneWidget);
    expect(find.text('Mismatch'), findsOneWidget);
    expect(find.text('+12.5% variance'), findsOneWidget);
    expect(find.text('• Amount variance of 12.5%'), findsOneWidget);
  });

  testWidgets('exposes one merged semantics label per warning', (tester) async {
    final handle = tester.ensureSemantics();
    await tester.pumpWidget(_host(
      const InvoiceWarningsPanel(
        warnings: [
          InvoiceWarning(
            type: 'missing_field',
            severity: WarningSeverity.error,
            message: 'Missing vendor name',
          ),
        ],
        poMatch: null,
      ),
    ));
    expect(
      find.bySemanticsLabel('Error: Missing vendor name'),
      findsOneWidget,
    );
    await expectLater(tester, meetsGuideline(textContrastGuideline));
    handle.dispose();
  });
}
