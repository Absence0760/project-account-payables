import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/l10n/gen/app_localizations.dart';
import 'package:ap_mobile/models/audit_entry.dart';
import 'package:ap_mobile/models/exception.dart';
import 'package:ap_mobile/models/invoice.dart';
import 'package:ap_mobile/models/vendor.dart';
import 'package:ap_mobile/screens/approvals_screen.dart';
import 'package:ap_mobile/screens/exceptions_screen.dart';
import 'package:ap_mobile/screens/invoices_screen.dart';
import 'package:ap_mobile/screens/login_screen.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/exception_store.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/widgets/activity_timeline.dart';
import 'package:ap_mobile/widgets/advanced_search_sheet.dart';
import 'package:ap_mobile/widgets/erp_status_panel.dart';
import 'package:ap_mobile/widgets/exception_list_tile.dart';
import 'package:ap_mobile/widgets/exception_status_badge.dart';
import 'package:ap_mobile/widgets/invoice_edit_sheet.dart';
import 'package:ap_mobile/widgets/invoice_list_tile.dart';
import 'package:ap_mobile/widgets/invoice_warnings_panel.dart';
import 'package:ap_mobile/widgets/kpi_card.dart';
import 'package:ap_mobile/widgets/status_badge.dart';
import 'package:ap_mobile/widgets/vendor_list_tile.dart';
import 'package:ap_mobile/widgets/vendor_status_badge.dart';

// Regression guard mirroring the web axe pass: every key surface must clear
// Flutter's built-in accessibility guidelines — minimum tap-target size
// (WCAG 2.5.8), labelled tappables (WCAG 4.1.2 / 1.1.1) and text contrast
// (WCAG 1.4.3). Run with `flutter test test/a11y/`.

Widget _host(Widget child) => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(body: child),
    );

Invoice _invoice() => Invoice(
      id: 'inv1',
      invoiceNumber: 'INV-001',
      vendorName: 'Acme Supplies',
      amount: 1500,
      currency: 'USD',
      status: InvoiceStatus.readyForReview,
      dueDate: DateTime(2026, 2, 1),
      createdAt: DateTime(2026, 1, 1),
    );

ApException _exception() => ApException(
      id: 'exc1',
      invoiceId: 'inv1',
      invoiceNumber: 'INV-001',
      vendorName: 'Acme Supplies',
      amount: 1500,
      exceptionType: 'duplicate',
      typeLabel: 'Duplicate Invoice',
      severity: ApExceptionSeverity.error,
      status: ApExceptionStatus.open,
      createdAt: DateTime(2026, 1, 1),
    );

http.Response _list(List<Map<String, dynamic>> items) => http.Response(
      jsonEncode({'invoices': items}),
      200,
      headers: {'content-type': 'application/json'},
    );

http.Response _wrappedList(List<Map<String, dynamic>> items) => http.Response(
      jsonEncode({'items': items, 'total': items.length, 'page': 1}),
      200,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _exceptionJson(String id) => {
      'id': id,
      'invoice_id': 'inv-$id',
      'invoice_number': 'INV-$id',
      'vendor_name': 'Acme Corp',
      'amount': 250,
      'exception_type': 'duplicate',
      'type_label': 'Duplicate Invoice',
      'severity': 'error',
      'status': 'open',
      'is_overdue': false,
      'created_at': '2026-01-01T12:00:00',
    };

Map<String, dynamic> _invoiceJson(String id) => {
      'id': id,
      'invoice_number': 'INV-$id',
      'vendor_name': 'Acme Corp',
      'amount': 100,
      'currency': 'USD',
      'status': 'ready_for_review',
      'created_at': '2026-01-01T12:00:00',
    };

Future<void> _pumpUntil(WidgetTester tester, Finder finder) async {
  for (var i = 0; i < 30 && finder.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  group('InvoiceListTile', () {
    testWidgets('meets tap-target, label and contrast guidelines',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        InvoiceListTile(invoice: _invoice(), onTap: () {}),
      ));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });

    testWidgets('announces a single composed label (vendor, amount, status)',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        InvoiceListTile(invoice: _invoice(), onTap: () {}),
      ));
      // The merged announcement leads with vendor + amount, not 5 fragments.
      expect(
        find.bySemanticsLabel(RegExp(r'Acme Supplies.*1,500.*Ready for Review')),
        findsOneWidget,
      );
      handle.dispose();
    });
  });

  group('KpiCard', () {
    testWidgets('meets contrast guideline and merges into one label',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        const KpiCard(
          title: 'For Review',
          value: '7',
          subtitle: '3 overdue',
          icon: Icons.rate_review,
        ),
      ));

      await expectLater(tester, meetsGuideline(textContrastGuideline));
      expect(find.bySemanticsLabel('For Review: 7, 3 overdue'), findsOneWidget);
      handle.dispose();
    });
  });

  group('StatusBadge', () {
    testWidgets('exposes its status as a label and clears contrast',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        const StatusBadge(status: InvoiceStatus.pending),
      ));

      await expectLater(tester, meetsGuideline(textContrastGuideline));
      expect(find.bySemanticsLabel('Status: Pending'), findsOneWidget);
      handle.dispose();
    });

    // The amber/orange/red hues are the worst-case for AA on the pale tint —
    // guard each so a future "make it amber again" regression is caught.
    for (final status in [
      InvoiceStatus.readyForReview, // amber tint
      InvoiceStatus.pending, // orange tint
      InvoiceStatus.rejected, // red tint
      InvoiceStatus.approved, // green tint
    ]) {
      testWidgets('clears contrast for ${status.value}', (tester) async {
        final handle = tester.ensureSemantics();
        await tester.pumpWidget(_host(StatusBadge(status: status)));
        await expectLater(tester, meetsGuideline(textContrastGuideline));
        handle.dispose();
      });
    }
  });

  group('VendorListTile', () {
    Vendor vendor() => Vendor(
          id: 'v1',
          name: 'Acme Supplies',
          code: 'ACME',
          email: 'ap@acme.com',
          status: VendorStatus.unverified,
          source: 'manual',
          invoiceCount: 3,
        );

    testWidgets('meets tap-target, label and contrast guidelines',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        VendorListTile(vendor: vendor(), onTap: () {}),
      ));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // One merged announcement leads with the vendor name + status.
      expect(
        find.bySemanticsLabel(RegExp(r'Acme Supplies.*Unverified.*invoices')),
        findsOneWidget,
      );
      handle.dispose();
    });
  });

  group('VendorStatusBadge', () {
    // Amber/red are the worst case for AA on the pale tint — guard each.
    for (final status in VendorStatus.values) {
      testWidgets('exposes label and clears contrast for ${status.value}',
          (tester) async {
        final handle = tester.ensureSemantics();
        await tester.pumpWidget(_host(VendorStatusBadge(status: status)));
        await expectLater(tester, meetsGuideline(textContrastGuideline));
        expect(
          find.bySemanticsLabel('Status: ${status.label}'),
          findsOneWidget,
        );
        handle.dispose();
      });
    }
  });

  group('ActivityTimeline', () {
    AuditEntry auditEntry({
      String action = 'invoice.edited',
      Map<String, dynamic>? details = const {
        'changes': {
          'amount': {'old': '100.00', 'new': '250.00'},
        },
      },
    }) =>
        AuditEntry(
          id: 'a1',
          actorId: 'u1',
          actorName: 'Demo User',
          action: action,
          entityType: 'invoice',
          entityId: '1',
          details: details,
          createdAt: DateTime(2026, 1, 2, 10),
        );

    testWidgets('clears contrast and announces one phrase per entry',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        ActivityTimeline(entries: [auditEntry()]),
      ));

      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // Each entry merges into a single sensible announcement.
      expect(
        find.bySemanticsLabel(
          RegExp(r'Edited fields by Demo User.*changed from 100.00 to 250.00'),
        ),
        findsOneWidget,
      );
      handle.dispose();
    });
  });

  group('InvoiceEditSheet', () {
    Invoice inv() => Invoice(
          id: '1',
          invoiceNumber: 'INV-001',
          vendorName: 'Acme Supplies',
          amount: 1500,
          currency: 'USD',
          status: InvoiceStatus.readyForReview,
          createdAt: DateTime(2026, 1, 1),
        );

    testWidgets('meets tap-target, label and contrast guidelines',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(InvoiceEditSheet(invoice: inv())));
      await tester.pump();

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // The icon-only close + date-clear controls announce their purpose.
      expect(find.bySemanticsLabel('Close edit form'), findsOneWidget);
      handle.dispose();
    });
  });

  group('InvoiceWarningsPanel', () {
    testWidgets('clears contrast and merges each warning into one label',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        const InvoiceWarningsPanel(
          warnings: [
            InvoiceWarning(
              type: 'missing_field',
              severity: WarningSeverity.error,
              message: 'Missing vendor name',
            ),
            InvoiceWarning(
              type: 'duplicate',
              severity: WarningSeverity.warning,
              message: 'Duplicate invoice number',
            ),
          ],
          poMatch: PoMatch(
            matchType: '3-way',
            status: 'mismatch',
            variancePct: 12.5,
            withinTolerance: false,
            issues: ['Amount variance'],
          ),
        ),
      ));

      await expectLater(tester, meetsGuideline(textContrastGuideline));
      expect(find.bySemanticsLabel('Error: Missing vendor name'),
          findsOneWidget);
      handle.dispose();
    });
  });

  group('ErpStatusPanel', () {
    Invoice erpInvoice() => Invoice(
          id: 'inv1',
          invoiceNumber: 'INV-001',
          vendorName: 'Acme',
          amount: 100,
          currency: 'USD',
          status: InvoiceStatus.failed,
          createdAt: DateTime(2026, 1, 1),
        );

    testWidgets('clears contrast with an error row', (tester) async {
      final handle = tester.ensureSemantics();
      final info = ErpInfo.fromAuditLog([
        AuditEntry(
          id: 'a1',
          actorName: 'Demo User',
          action: 'invoice.erp_failed',
          details: const {'error': 'Connection refused'},
          createdAt: DateTime(2026, 1, 2),
        ),
      ]);
      await tester.pumpWidget(_host(
        ErpStatusPanel(invoice: erpInvoice(), erpInfo: info),
      ));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });
  });

  group('AdvancedSearchSheet', () {
    testWidgets('meets tap-target, label and contrast guidelines',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        const AdvancedSearchSheet(initial: InvoiceSearchFilters.empty),
      ));
      await tester.pump();

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // The icon-only close control announces its purpose.
      expect(find.bySemanticsLabel('Close advanced search'), findsOneWidget);
      handle.dispose();
    });
  });

  group('LoginScreen', () {
    setUp(() {
      FlutterSecureStorage.setMockInitialValues({});
      ApiClient().debugConfigure();
    });

    testWidgets('meets tap-target, label and contrast guidelines',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(const MaterialApp(home: LoginScreen()));
      await tester.pump();

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });

    testWidgets('the password show/hide toggle exposes an accessible label',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(const MaterialApp(home: LoginScreen()));
      await tester.pump();

      // The icon-only visibility toggle must announce its purpose (WCAG 4.1.2).
      expect(find.bySemanticsLabel('Show password'), findsOneWidget);
      // Toggling flips the label so its state is conveyed too.
      await tester.tap(find.byTooltip('Show password'));
      await tester.pump();
      expect(find.bySemanticsLabel('Hide password'), findsOneWidget);
      handle.dispose();
    });
  });

  group('InvoicesScreen', () {
    setUpAll(() async {
      OfflineStore.instance.debugUseMemory();
    });

    setUp(() async {
      InvoiceStore.instance.debugReset();
      FlutterSecureStorage.setMockInitialValues({});
      await OfflineStore.instance.clear();
      ApiClient().debugConfigure();
    });

    testWidgets('the capture-invoice app-bar action exposes a label',
        (tester) async {
      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([])),
      );

      await tester.pumpWidget(MaterialApp(
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const InvoicesScreen(),
      ));
      await tester.pump();

      expect(find.bySemanticsLabel('Capture invoice'), findsOneWidget);
      handle.dispose();
    });
  });

  group('ApprovalsScreen', () {
    setUpAll(() async {
      OfflineStore.instance.debugUseMemory();
    });

    setUp(() async {
      InvoiceStore.instance.debugReset();
      FlutterSecureStorage.setMockInitialValues({});
      await OfflineStore.instance.clear();
      ApiClient().debugConfigure();
    });

    testWidgets('approve/reject affordances meet tap-target + label guidelines',
        (tester) async {
      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient((req) async => _list([_invoiceJson('1')])),
      );

      await tester.pumpWidget(const MaterialApp(home: ApprovalsScreen()));
      await _pumpUntil(tester, find.byType(InvoiceListTile));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });
  });

  group('ExceptionListTile', () {
    testWidgets('meets tap-target, label and contrast guidelines',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        ExceptionListTile(exception: _exception(), onTap: () {}),
      ));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });

    testWidgets('announces a single composed label (type, invoice, status)',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        ExceptionListTile(exception: _exception(), onTap: () {}),
      ));
      expect(
        find.bySemanticsLabel(
          RegExp(r'Duplicate Invoice.*INV-001.*Open'),
        ),
        findsOneWidget,
      );
      handle.dispose();
    });
  });

  group('ExceptionStatusBadge', () {
    // Open (deep-amber) / escalated (red) are the worst-case hues for AA on the
    // pale tint — guard each.
    for (final status in ApExceptionStatus.values) {
      testWidgets('exposes label and clears contrast for ${status.value}',
          (tester) async {
        final handle = tester.ensureSemantics();
        await tester.pumpWidget(_host(ExceptionStatusBadge(status: status)));
        await expectLater(tester, meetsGuideline(textContrastGuideline));
        expect(find.bySemanticsLabel('Status: ${status.label}'),
            findsOneWidget);
        handle.dispose();
      });
    }
  });

  group('ExceptionsScreen', () {
    setUpAll(() async {
      OfflineStore.instance.debugUseMemory();
    });

    setUp(() async {
      ExceptionStore.instance.debugReset();
      FlutterSecureStorage.setMockInitialValues({});
      await OfflineStore.instance.clear();
      ApiClient().debugConfigure();
      ExceptionStore.instance.setStatusFilter(null);
    });

    testWidgets('the loaded queue meets tap-target + label + contrast',
        (tester) async {
      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient((req) async => _wrappedList([_exceptionJson('1')])),
      );

      await tester.pumpWidget(const MaterialApp(home: ExceptionsScreen()));
      await _pumpUntil(tester, find.byType(ExceptionListTile));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });
  });
}
