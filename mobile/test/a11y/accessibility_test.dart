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
import 'package:ap_mobile/models/notification.dart';
import 'package:ap_mobile/models/vendor.dart';
import 'package:ap_mobile/screens/admin_users_screen.dart';
import 'package:ap_mobile/screens/approvals_screen.dart';
import 'package:ap_mobile/screens/cash_flow_screen.dart';
import 'package:ap_mobile/screens/org_settings_screen.dart';
import 'package:ap_mobile/screens/exception_detail_screen.dart';
import 'package:ap_mobile/screens/exceptions_screen.dart';
import 'package:ap_mobile/screens/invoices_screen.dart';
import 'package:ap_mobile/screens/login_screen.dart';
import 'package:ap_mobile/screens/notifications_screen.dart';
import 'package:ap_mobile/screens/workflows_screen.dart';
import 'package:ap_mobile/services/offline_store.dart';
import 'package:ap_mobile/stores/admin_user_store.dart';
import 'package:ap_mobile/stores/cash_flow_store.dart';
import 'package:ap_mobile/stores/exception_store.dart';
import 'package:ap_mobile/stores/org_settings_store.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/stores/notification_store.dart';
import 'package:ap_mobile/stores/workflow_store.dart';
import 'package:ap_mobile/widgets/activity_timeline.dart';
import 'package:ap_mobile/widgets/advanced_search_sheet.dart';
import 'package:ap_mobile/widgets/bulk_action_bar.dart';
import 'package:ap_mobile/widgets/erp_status_panel.dart';
import 'package:ap_mobile/widgets/exception_list_tile.dart';
import 'package:ap_mobile/widgets/exception_status_badge.dart';
import 'package:ap_mobile/widgets/invoice_edit_sheet.dart';
import 'package:ap_mobile/widgets/invoice_list_tile.dart';
import 'package:ap_mobile/widgets/invoice_warnings_panel.dart';
import 'package:ap_mobile/widgets/kpi_card.dart';
import 'package:ap_mobile/widgets/notification_bell.dart';
import 'package:ap_mobile/widgets/notification_list_tile.dart';
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

/// Like [_host] but mounts [screen] as the MaterialApp `home` directly (no extra
/// Scaffold), for full screens that bring their own. Carries the localization
/// delegates so a localized screen's `AppLocalizations.of(context)` resolves.
Widget _screenHost(Widget screen) => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: screen,
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

AppNotification _notification({bool read = false}) => AppNotification(
      id: 'n1',
      eventType: 'invoice_approved',
      entityType: 'invoice',
      entityId: 'inv1',
      title: 'Invoice approved',
      body: 'INV-001 was approved by Demo User',
      readAt: read ? DateTime(2026, 1, 2) : null,
      createdAt: DateTime(2026, 1, 1),
    );

http.Response _notificationPage(List<Map<String, dynamic>> items) =>
    http.Response(
      jsonEncode({
        'items': items,
        'total': items.length,
        'unread': items.where((i) => i['read_at'] == null).length,
        'page': 1,
        'page_size': 20,
      }),
      200,
      headers: {'content-type': 'application/json'},
    );

Map<String, dynamic> _notificationJson(String id) => {
      'id': id,
      'event_type': 'invoice_approved',
      'entity_type': 'invoice',
      'entity_id': 'inv-$id',
      'title': 'Invoice approved',
      'body': 'INV-$id was approved',
      'read_at': null,
      'created_at': '2026-01-01T12:00:00Z',
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

    testWidgets('selection mode exposes a checked state + keeps tap target',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        InvoiceListTile(
          invoice: _invoice(),
          selectionMode: true,
          selected: true,
          onTap: () {},
        ),
      ));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // The merged row label leads with the selection state for the reader.
      expect(
        find.bySemanticsLabel(RegExp(r'Selected.*Acme Supplies')),
        findsOneWidget,
      );
      handle.dispose();
    });
  });

  group('BulkActionBar', () {
    testWidgets('labels the count + actions and clears contrast',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        BulkActionBar(
          selectedCount: 3,
          onExport: () {},
          onStatusChange: () {},
          onDelete: () {},
        ),
      ));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      expect(find.bySemanticsLabel('3 selected'), findsOneWidget);
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
      await tester.pumpWidget(_host(const LoginScreen()));
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
      await tester.pumpWidget(_host(const LoginScreen()));
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
      InvoiceStore.instance.reset();
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
      InvoiceStore.instance.reset();
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

      await tester.pumpWidget(_screenHost(const ApprovalsScreen()));
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
      ExceptionStore.instance.reset();
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

      await tester.pumpWidget(_host(const ExceptionsScreen()));
      await _pumpUntil(tester, find.byType(ExceptionListTile));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });
  });

  group('ExceptionListTile (selection mode)', () {
    testWidgets('a selected row exposes a checked state + keeps its tap target',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        ExceptionListTile(
          exception: _exception(),
          selected: true,
          onTap: () {},
          onLongPress: () {},
        ),
      ));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // The merged row label still announces the exception.
      expect(
        find.bySemanticsLabel(RegExp(r'Duplicate Invoice.*Open')),
        findsOneWidget,
      );
      handle.dispose();
    });
  });

  group('ExceptionDetailScreen', () {
    setUp(() async {
      ExceptionStore.instance.reset();
      FlutterSecureStorage.setMockInitialValues({});
      ApiClient().debugConfigure();
    });

    testWidgets('the loaded detail meets tap-target + label + contrast',
        (tester) async {
      // Tall surface so the whole detail list (incl. action buttons) builds.
      tester.view.physicalSize = const Size(1200, 2400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient(
          (req) async => http.Response(
            jsonEncode(_exceptionJson('1')),
            200,
            headers: {'content-type': 'application/json'},
          ),
        ),
      );

      await tester.pumpWidget(
        _host(const ExceptionDetailScreen(exceptionId: '1')),
      );
      await _pumpUntil(tester, find.text('Duplicate Invoice'));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });
  });

  group('NotificationListTile', () {
    testWidgets('meets tap-target, label and contrast guidelines',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        NotificationListTile(notification: _notification(), onTap: () {}),
      ));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });

    testWidgets('announces one merged label leading with unread + event',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        NotificationListTile(notification: _notification(), onTap: () {}),
      ));
      expect(
        find.bySemanticsLabel(
          RegExp(r'Unread.*Invoice approved.*was approved'),
        ),
        findsOneWidget,
      );
      handle.dispose();
    });

    // A read row must still clear contrast (greyed glyph + muted title).
    testWidgets('a read row clears contrast', (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        NotificationListTile(notification: _notification(read: true)),
      ));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });
  });

  group('NotificationBell', () {
    setUp(() {
      NotificationStore.instance.reset();
      FlutterSecureStorage.setMockInitialValues({});
      ApiClient().debugConfigure();
    });

    testWidgets('exposes an accessible label including the unread count',
        (tester) async {
      final handle = tester.ensureSemantics();
      // Seed the store with 3 unread so the badge renders.
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response(
              jsonEncode({'unread': 3}),
              200,
              headers: {'content-type': 'application/json'},
            )),
      );
      await tester.pumpWidget(_host(const NotificationBell()));
      // Wait on the real rendered signal — the badge label appearing — not the
      // store field, so the ListenableBuilder rebuild that carries the count
      // into the Semantics label is guaranteed flushed before we assert.
      await _pumpUntil(tester, find.bySemanticsLabel('Notifications, 3 unread'));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      expect(find.bySemanticsLabel('Notifications, 3 unread'), findsOneWidget);
      handle.dispose();
    });
  });

  group('NotificationsScreen', () {
    setUpAll(() async {
      OfflineStore.instance.debugUseMemory();
    });

    setUp(() async {
      NotificationStore.instance.reset();
      FlutterSecureStorage.setMockInitialValues({});
      await OfflineStore.instance.clear();
      ApiClient().debugConfigure();
    });

    testWidgets('the loaded center meets tap-target + label + contrast',
        (tester) async {
      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient(
          (req) async => _notificationPage([_notificationJson('1')]),
        ),
      );

      await tester.pumpWidget(_host(const NotificationsScreen()));
      await _pumpUntil(tester, find.byType(NotificationListTile));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // The mark-all-read action announces its purpose (WCAG 4.1.2).
      expect(find.bySemanticsLabel('Mark all notifications read'),
          findsOneWidget);
      handle.dispose();
    });
  });

  group('CashFlowScreen', () {
    setUp(() {
      CashFlowStore.instance.reset();
      ApiClient().debugConfigure();
    });

    testWidgets('the loaded forecast (with a breach) meets tap-target + '
        'label + contrast', (tester) async {
      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.url.path.endsWith('/analytics/cashflow_forecast')) {
            return http.Response(
              jsonEncode({
                'granularity': 'week',
                'horizon_days': 90,
                'periods': [
                  {
                    'period': '2026-W26',
                    'scheduled_amount': 3000.0,
                    'committed_amount': 2000.0,
                    'pending_amount': 1000.0,
                    'discount_eligible_amount': 0.0,
                    'count': 4,
                  },
                ],
                'totals': {
                  'scheduled_amount': 3000.0,
                  'committed_amount': 2000.0,
                  'pending_amount': 1000.0,
                  'discount_eligible_amount': 0.0,
                  'count': 4,
                },
              }),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          // cash_position with a breached period (exercises the red contrast).
          return http.Response(
            jsonEncode({
              'granularity': 'week',
              'horizon_days': 90,
              'opening_balance': 6000.0,
              'opening_balance_source': 'settings',
              'threshold': 5000.0,
              'periods': [
                {
                  'period': '2026-W26',
                  'opening': 6000.0,
                  'outflow': 4000.0,
                  'inflow': 0.0,
                  'closing': 2000.0,
                  'below_threshold': true,
                },
              ],
              'breaches': [
                {'period': '2026-W26', 'closing': 2000.0, 'shortfall': 3000.0},
              ],
            }),
            200,
            headers: {'content-type': 'application/json'},
          );
        }),
      );

      await tester.pumpWidget(_host(const CashFlowScreen()));
      await _pumpUntil(tester, find.byType(KpiCard));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      // Catches sub-AA red/grey money + alert text (textContrastGuideline is
      // strict — it caught earlier muted-grey defects in this suite).
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // The low-balance alert exposes one merged announcement (WCAG 1.3.1).
      expect(find.bySemanticsLabel(RegExp('^Low balance alert')),
          findsOneWidget);
      handle.dispose();
    });
  });

  group('AdminUsersScreen', () {
    setUp(() {
      AdminUserStore.instance.reset();
      FlutterSecureStorage.setMockInitialValues({});
      ApiClient().debugConfigure();
    });

    testWidgets('the loaded user list meets tap-target + label + contrast',
        (tester) async {
      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.url.path.endsWith('/admin/roles')) {
            return http.Response(
              jsonEncode([
                {'id': 'r1', 'name': 'admin', 'is_system': true},
              ]),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          return http.Response(
            jsonEncode({
              'items': [
                {
                  'id': 'u1',
                  'email': 'alice@acme.com',
                  'full_name': 'Alice Admin',
                  'is_active': true,
                  'roles': [
                    {'id': 'r1', 'name': 'admin'},
                  ],
                  'created_at': '2026-01-01T00:00:00',
                },
                {
                  'id': 'u2',
                  'email': 'bob@acme.com',
                  'full_name': 'Bob Clerk',
                  'is_active': false,
                  'roles': [
                    {'id': 'r3', 'name': 'ap_clerk'},
                  ],
                  'created_at': '2026-01-01T00:00:00',
                },
              ],
              'total': 2,
              'page': 1,
            }),
            200,
            headers: {'content-type': 'application/json'},
          );
        }),
      );

      await tester.pumpWidget(_host(const AdminUsersScreen()));
      await _pumpUntil(tester, find.text('Alice Admin'));

      // Contrast covers the small Inactive badge + role chips + muted email.
      // (Tap-target isn't asserted at the screen level here: the Material
      // `SearchBar` in the app bar is a framework 24px field — the same one the
      // vendors/invoices screens use — so it's exercised via the tile, not the
      // whole-screen sweep, matching the existing InvoicesScreen a11y test.)
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // The deactivated user's row merges into one announcement carrying
      // "inactive" (so the Inactive badge isn't an unlabelled colour cue).
      expect(
        find.bySemanticsLabel(RegExp(r'Bob Clerk.*inactive')),
        findsOneWidget,
      );
      handle.dispose();
    });

    testWidgets('the create-user sheet meets tap-target + label + contrast',
        (tester) async {
      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient((req) async {
          if (req.url.path.endsWith('/admin/roles')) {
            return http.Response(
              jsonEncode([
                {'id': 'r1', 'name': 'admin', 'is_system': true},
                {'id': 'r3', 'name': 'ap_clerk', 'is_system': true},
              ]),
              200,
              headers: {'content-type': 'application/json'},
            );
          }
          return http.Response(
            jsonEncode({'items': [], 'total': 0, 'page': 1}),
            200,
            headers: {'content-type': 'application/json'},
          );
        }),
      );

      await tester.pumpWidget(_host(const AdminUsersScreen()));
      await _pumpUntil(tester, find.text('No users found'));

      await tester
          .tap(find.widgetWithText(FloatingActionButton, 'Create user'));
      await tester.pumpAndSettle();

      // The form fields + role checkboxes + Create/Cancel actions clear the
      // tap-target, label and contrast guidelines.
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      expect(find.text('New user'), findsOneWidget);
      handle.dispose();
    });
  });

  group('OrgSettingsScreen', () {
    setUp(() {
      OrgSettingsStore.instance.reset();
      FlutterSecureStorage.setMockInitialValues({});
      ApiClient().debugConfigure();
    });

    testWidgets('the settings form meets tap-target + label + contrast',
        (tester) async {
      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response(
              jsonEncode({
                'id': 'org1',
                'name': 'Acme Corp',
                'slug': 'acme',
                'plan': 'pro',
                'created_at': '2026-01-01T00:00:00',
                'settings': {
                  'company': {'address': '1 Main St'},
                  'invoice_defaults': {'currency': 'USD'},
                },
              }),
              200,
              headers: {'content-type': 'application/json'},
            )),
      );

      await tester.pumpWidget(_host(const OrgSettingsScreen()));
      await _pumpUntil(tester, find.text('Acme Corp'));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      handle.dispose();
    });
  });

  group('WorkflowsScreen', () {
    setUp(() {
      WorkflowStore.instance.reset();
      FlutterSecureStorage.setMockInitialValues({});
      ApiClient().debugConfigure();
    });

    testWidgets('the loaded list meets tap-target + label + contrast',
        (tester) async {
      final handle = tester.ensureSemantics();
      ApiClient().debugConfigure(
        client: MockClient((req) async => http.Response(
              jsonEncode({
                'items': [
                  {
                    'id': 'wf1',
                    'name': 'Default Workflow',
                    'is_active': true,
                    'is_default': true,
                    'steps_config': {
                      'steps': [
                        {
                          'number': 1,
                          'type': 'extraction',
                          'name': 'Extract',
                          'config': {},
                        },
                      ],
                    },
                    'created_at': '2026-01-01T00:00:00',
                  },
                  {
                    'id': 'wf2',
                    'name': 'Rush Approval',
                    'is_active': false,
                    'is_default': false,
                    'steps_config': {'steps': []},
                    'created_at': '2026-01-01T00:00:00',
                  },
                ],
                'total': 2,
                'page': 1,
              }),
              200,
              headers: {'content-type': 'application/json'},
            )),
      );

      await tester.pumpWidget(_host(const WorkflowsScreen()));
      await _pumpUntil(tester, find.text('Default Workflow'));

      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      // Covers the Active/Inactive + Default badges (darkened-accent text).
      await expectLater(tester, meetsGuideline(textContrastGuideline));
      // The inactive row merges into one announcement carrying "Inactive" so
      // the badge isn't an unlabelled colour cue.
      expect(
        find.bySemanticsLabel(RegExp(r'Rush Approval.*Inactive')),
        findsOneWidget,
      );
      handle.dispose();
    });
  });
}
