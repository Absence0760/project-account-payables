# Mobile — CLAUDE.md

Mobile-specific guidance. See root `CLAUDE.md` for project-wide context.

## Stack

- **Flutter 3.41+**, Dart 3.11+
- **iOS + Android** (single Dart codebase, dual platform targets)
- **State management**: StatefulWidget + setState + ChangeNotifier singletons (no Bloc/Provider/Riverpod)
- **HTTP**: `http` package + `flutter_secure_storage` for JWT
- **Styling**: Material 3 with blue seed color

## Commands (from `mobile/`)

```bash
flutter pub get              # install dependencies
flutter run                  # run on connected device/simulator
flutter build ios            # production iOS build
flutter build apk            # production Android APK
flutter build appbundle      # production Android App Bundle (Play Store)
flutter analyze              # lint/analyze
flutter test                 # run tests
```

## Project structure

```
mobile/
├── lib/
│   ├── main.dart                # App entry, splash, biometric check, push init
│   ├── config.dart              # API URL, tenant slug
│   ├── api/
│   │   ├── api_client.dart      # HTTP client (JWT + X-Tenant-Slug header, timeout, debug logs)
│   │   └── endpoints.dart       # Typed API methods (auth, invoices, dashboard, payments)
│   ├── models/
│   │   ├── user.dart            # User model with role helpers
│   │   ├── audit_entry.dart     # AuditEntry + AuditFieldChange (invoice activity timeline; details.changes diff)
│   │   ├── invoice.dart         # Invoice, InvoiceStatus enum (12 states); isEditable gate mirrors backend IMMUTABLE_STATUSES
│   │   ├── exception.dart       # ApException, ApExceptionStatus + ApExceptionSeverity enums
│   │   ├── payment.dart         # Payment, PaymentMethod, DashboardData, aging, trends
│   │   ├── payment_queue.dart   # PaymentQueueItem, PaymentSummary, PaymentRun, PaymentRunSelection (money as display strings — no client float math)
│   │   └── vendor.dart          # Vendor, VendorStatus enum (active/unverified/inactive/rejected)
│   ├── services/
│   │   ├── biometric_service.dart  # Face ID / fingerprint via local_auth
│   │   ├── camera_capture.dart     # Image picker (camera/gallery) + file picker (PDF/PNG/JPG/TIFF) + invoice upload
│   │   ├── offline_store.dart      # SQLite cache for offline viewing
│   │   └── push_service.dart       # Firebase Cloud Messaging + local notifications
│   ├── stores/
│   │   ├── auth_store.dart      # Auth state — login, logout, role checks
│   │   ├── invoice_store.dart   # Invoice list, filter, approve/reject (offline cached)
│   │   ├── exception_store.dart # Exception list, filter, resolve/escalate/dismiss (offline cached)
│   │   ├── dashboard_store.dart # Dashboard KPI data (offline cached)
│   │   ├── vendor_store.dart    # Vendor list, filter/search, verify/reject, ERP sync (offline cached)
│   │   └── payment_queue_store.dart # Payment queue + summary + runs; per-row method selection; create/execute/cancel runs
│   ├── screens/
│   │   ├── login_screen.dart    # Tenant + email/password login
│   │   ├── home_screen.dart     # Bottom nav host (role-aware tabs)
│   │   ├── dashboard_screen.dart # KPIs, aging, top vendors
│   │   ├── invoices_screen.dart  # Invoice list with search + status filters + advanced-search (tune) action + camera button
│   │   ├── invoice_detail_screen.dart # Detail view with approve/reject + edit affordance + warnings/fraud + PO match + ERP status + activity timeline + file preview (image thumbnail / PDF card) → full viewer
│   │   ├── approvals_screen.dart # Pending approvals with swipe-to-approve
│   │   ├── exceptions_screen.dart # Exception queue — filter + swipe/sheet resolve/escalate/dismiss
│   │   ├── capture_screen.dart   # Camera/gallery capture + file picker (PDF/PNG/JPG/TIFF) → upload → extract
│   │   ├── payments_screen.dart  # Payment history
│   │   ├── vendors_screen.dart   # Vendor management — list + search/status filters, swipe/sheet verify+reject (unverified only), ERP-sync app-bar action (all admin/ap_manager-gated)
│   │   ├── payment_queue_screen.dart # Pay — Queue tab (select approved invoices + per-row method → Create Run) + Runs tab (execute/cancel drafts), KPI summary bar
│   │   └── settings_screen.dart  # User profile, biometric toggle, logout
│   └── widgets/
│       ├── activity_timeline.dart # Invoice audit-log timeline (action label, actor, time, per-field before→after diff); empty state; one merged Semantics label per entry
│       ├── advanced_search_sheet.dart # Modal bottom-sheet advanced search (vendor, PO, amount range, due-date range); seeded from live filters; min≤max + decimal validation; returns InvoiceSearchFilters (Apply) / empty (Clear) / null (dismiss)
│       ├── invoice_warnings_panel.dart # Detail-screen warnings/fraud flags (severity-coloured) + PO-match panel (match type, status, variance %, issues); one merged Semantics label per warning
│       ├── erp_status_panel.dart # Detail-screen ERP status — ErpInfo.fromAuditLog derives ERP reference / document id / send error from the audit log; shown for ERP-bound + ERP-failed statuses
│       ├── invoice_file_viewer.dart # Full-screen uploaded-file viewer — images via Image.network (auth headers), PDFs fetched as bytes via ApiClient.getBytes + rendered with pdfx; isPdf/absoluteUrl helpers; loading/error/Retry states
│       ├── invoice_edit_sheet.dart # Modal bottom-sheet edit form (vendor, invoice #, amount, PO, GL, description, due date); returns the partial diff; amount sent as string-Decimal
│       ├── status_badge.dart    # Colored invoice status chip
│       ├── exception_status_badge.dart # Colored exception status chip (open/escalated/resolved/dismissed)
│       ├── exception_list_tile.dart    # Exception row with type, invoice, severity, status
│       ├── kpi_card.dart        # Dashboard metric card
│       ├── invoice_list_tile.dart # Invoice row with vendor, amount, status
│       ├── vendor_status_badge.dart # Colored vendor status chip (active/unverified/inactive/rejected)
│       └── vendor_list_tile.dart # Vendor row with name, code/email, status, invoice count
├── test/                        # Unit and widget tests
├── ios/                         # Xcode project (auto-managed by Flutter)
├── android/                     # Gradle project (auto-managed by Flutter)
├── pubspec.yaml                 # Dependencies
└── analysis_options.yaml        # Lint rules
```

## Architecture patterns

- **ChangeNotifier singletons** for stores — `AuthStore.instance`, `InvoiceStore.instance`, etc.
- **ListenableBuilder** in widgets to react to store changes
- **No DI framework** — stores are static singletons
- **API client singleton** — `ApiClient()` auto-adds JWT and `X-Tenant-Slug` header
- **Secure storage** — JWT token persisted in iOS Keychain via `flutter_secure_storage`

## API integration

The mobile app talks to the same FastAPI backend as the web frontend:

- API base URL: `http://localhost:8000` (configurable in `config.dart`)
- Auth: `POST /api/auth/login` → JWT stored in secure storage
- Tenant: entered on login screen → sent as `X-Tenant-Slug` header
- 401 responses auto-clear session and return to login

## Screens → API mappings

| Screen | API calls |
|--------|-----------|
| Login | `POST /api/auth/login`, `GET /api/auth/me` |
| Dashboard | `GET /api/dashboard` |
| Invoices | `GET /api/invoices` (advanced search adds `vendor` / `po_number` / `amount_min` / `amount_max` / `due_date_from` / `due_date_to`) |
| Invoice Detail | `GET /api/invoices/{id}` (carries `warnings` + `po_match`), `POST /api/invoices/{id}/approve`, `POST /api/invoices/{id}/reject`, `PATCH /api/invoices/{id}` (edit fields — admin/ap_manager/cfo, hidden in immutable statuses), `GET /api/invoices/{id}/audit-log` (activity timeline + ERP-status derivation, any authenticated role) |
| Approvals | `GET /api/invoices` (filtered to `ready_for_review`) |
| Exceptions | `GET /api/exceptions` (status filter), `POST /api/exceptions/{id}/resolve` (action=resolve\|escalate\|dismiss) |
| Payments | `GET /api/payments` |
| Vendors | `GET /api/vendors` (status/search filters), `POST /api/vendors/{id}/verify`, `POST /api/vendors/{id}/reject`, `POST /api/vendors/sync-erp` (mutations admin/ap_manager) |
| Pay (queue) | `GET /api/payments/queue`, `GET /api/payments/summary`, `GET /api/payments/runs/`, `POST /api/payments/runs` (create draft), `POST /api/payments/runs/{id}/execute`, `POST /api/payments/runs/{id}/cancel` (admin/ap_manager/cfo) |
| Settings | Uses cached auth state |

## Role-based UI

Bottom navigation adapts based on user roles (same as web frontend):

| Tab | Visible to |
|-----|-----------|
| Dashboard | All roles |
| Invoices | All roles |
| Approvals | Admin, AP Manager |
| Exceptions | Admin, AP Manager |
| Vendors | Admin, AP Manager, CFO (verify/reject + ERP sync: Admin, AP Manager only) |
| Pay | Admin, AP Manager, CFO |
| Payments | Admin, AP Manager, CFO |
| Settings | All roles |

## Feature status

**Done:**
- Login with tenant selection
- Dashboard (KPIs, aging buckets, top vendors)
- Invoice list with search + status filter chips
- Advanced search — `AdvancedSearchSheet` (app-bar `tune` action; a dot badge marks an active advanced filter) filters the list by vendor, PO number, amount range and due-date range via `InvoiceStore.setFilters` → `GET /api/invoices` (`vendor` / `po_number` / `amount_min` / `amount_max` / `due_date_from` / `due_date_to`). Seeded from the live filters; validates min ≤ max + plain-decimal amounts; Apply / Clear / dismiss. The advanced filters compose with the quick status chips + search box (all carried into the same request + offline cache key)
- Invoice detail with approve/reject
- Invoice warnings / fraud flags + PO match — `InvoiceWarningsPanel` on the detail screen renders `Invoice.warnings` (`{type, severity, message}`, severity-coloured to WCAG AA) and the `po_match` panel (match type, status, variance %, issues). Parity with the web invoice modal; nothing renders when there are no warnings and no PO
- ERP status — `ErpStatusPanel` shows the invoice's ERP integration status (ERP reference / document id / send error + last action). `ErpInfo.fromAuditLog` derives it from the already-loaded audit log (latest `invoice.erp_*` / `invoice.completed` entry), so no extra request. Shown for ERP-bound statuses (`sending_to_erp` / `sent_to_erp` / `posted_in_erp`) and ERP-failed invoices
- Invoice editing — edit-sheet on the detail screen (vendor, invoice #, amount, PO, GL account, description, due date) via `PATCH /api/invoices/{id}`; amount sent as string-Decimal (never a lossy float); input validation; RBAC-gated (admin/ap_manager/cfo, hidden for clerks) and hidden in immutable statuses (the backend would 409); save success/failure announced via `A11y.announce`
- Activity timeline — invoice audit log on the detail screen (`GET /api/invoices/{id}/audit-log`): action label, actor, timestamp, per-field before→after diff from `details.changes`; loading / empty / error states; one merged Semantics announcement per entry
- Approvals tab with swipe-to-approve
- Exception queue (list + status filter + resolve / escalate / dismiss via swipe + action sheet; admin / AP manager only)
- Payment history list
- Vendor management — `VendorsScreen` + `VendorStore` over `GET /api/vendors` with status filters + search; verify / reject an unverified vendor via swipe (verify ⟶ / reject ⟵) or the action sheet, and an ERP-sync app-bar action (`POST /api/vendors/sync-erp`). Read is admin/ap_manager/cfo; the mutating actions are gated to admin/ap_manager (mirrors `require_roles`) and simply hidden for CFO. Offline-cached list
- Payment queue + runs — `PaymentQueueScreen` + `PaymentQueueStore`. Queue tab lists approved invoices (`GET /api/payments/queue`), each row a checkbox + per-row method picker; the selection creates a draft run (`POST /api/payments/runs`). Runs tab lists runs (`GET /api/payments/runs/`) and executes / cancels drafts. A KPI summary bar (total paid / pending / queue / card rebates) sits above both (`GET /api/payments/summary`). CFO-approval-required runs surface the gate before an execute attempt. Money is rendered as server-supplied display strings — the device never does float arithmetic on money (totals are server-computed)
- Role-based bottom navigation
- Settings (profile, tenant info, logout)
- JWT in secure storage (iOS Keychain / Android Keystore)
- Camera OCR — snap photo or pick from gallery → upload → trigger AI extraction
- File upload via file picker — pick a PDF / PNG / JPG / TIFF document on the device (`CameraCapture.pickDocument` → `file_picker`) and upload it through the same `/api/invoices/upload` extraction pipeline as the camera path. The capture screen offers Camera / Gallery / Choose file; PDFs preview as a document card (no inline bitmap), images preview inline
- File viewer — the invoice detail screen previews the uploaded file (image thumbnail or a PDF card) and opens it full-screen via `InvoiceFileViewer`: images via `Image.network` (auth headers), PDFs fetched as bytes (`ApiClient.getBytes`, so the JWT + tenant headers are attached) and rendered with `pdfx`; loading / error / Retry states
- Push notifications — Firebase Cloud Messaging (foreground + background), no-op if Firebase not configured
- Offline mode — SQLite cache for dashboard and invoice list, serves cached data on network failure
- Biometric login — Face ID / fingerprint / device PIN, toggle in settings, checked on app launch

**Mobile-only features (not on web):**
- Camera OCR capture (snap photo → upload)
- Push notifications (FCM)
- Offline mode (SQLite cache)
- Biometric login (Face ID / fingerprint)
- Swipe-to-approve gesture

**Web features not yet on mobile (see `docs/roadmap.md` Priority 8):**
- **MFA** — `AuthStore.login()` only handles `TokenResponse`; if the backend returns `MFAChallengeResponse` (when `AP_MFA_ENABLED=true` and the user is enrolled or org-enforced), login throws. Mobile users can still sign in when MFA is off, but tenants with enforcement need a mobile MFA flow + a `/profile` enrollment screen.
- **Org Security settings** — the web `/organization` page exposes the `mfa.required` toggle; mobile has no equivalent.
- **OIDC SSO** — `Sign in with Okta/Microsoft` button is web-only.
- Workflow management (list, create, edit steps)
- Organization settings (company, ERP config, extraction config)
- Admin user management (create, edit, delete users, role assignment)
- Bulk operations (select multiple, delete, status change)
- Export (CSV, JSON, XML)

## Accessibility (WCAG 2.2 AA equivalent)

The app targets WCAG 2.2 AA / EU EAA / ADA via Flutter's accessibility APIs.
Follow these conventions on every new screen/widget:

- **Label every icon-only / custom tappable.** A bare tooltip is *not* reliably
  exposed as a screen-reader label on all platforms (verified — `IconButton`'s
  `tooltip` shows up via `find.byTooltip` but not `find.bySemanticsLabel`). Wrap
  icon-only buttons in `Semantics(label: ..., button: true, child: IconButton(...))`
  and keep the `tooltip` for sighted hover (e.g. the login password show/hide
  toggle, the invoices Capture action). Swipe-action affordances carry a visible
  text label beside the icon (see `approvals_screen._swipeBackground`).
- **Compose one announcement per row/card.** List tiles, KPI cards and status
  badges wrap their inner spans in `Semantics(label: '...', excludeSemantics: true)`
  so assistive tech reads one sensible phrase ("Acme Supplies, $1,500, invoice
  INV-001, Ready for Review") instead of 5 disjoint fragments. Status badges
  expose `'Status: <label>'`.
- **Live-region announcements** for state changes that aren't seamlessly spoken
  (toasts, a swiped row vanishing, inline form errors). Funnel them through
  `A11y.announce(context, message)` in `lib/utils/a11y.dart` — it uses the
  non-deprecated `SemanticsService.sendAnnouncement` and resolves
  `TextDirection` from the active `Directionality` (avoids the `intl`
  `TextDirection` name clash). Wired into the `_showSnack` helpers
  (invoice/contract detail), capture upload result, login error, and the
  approvals swipe-approve.
- **Colour contrast ≥4.5:1.** Status/payment badges render the text in a
  *darkened* variant (`.shade700`/`.shade800`/`.shade900`) of the accent over
  the 0.15-alpha tint — the full-saturation hue fails AA. Muted greys use
  `grey.shade700` (not `shade500`/`shade600`, which fail at 11-14px).
- **Decorative icons** (brand mark, placeholder camera glyph, aging dots) are
  wrapped in `ExcludeSemantics` so they aren't announced.
- **Tap targets ≥48dp** — use `IconButton` defaults; don't shrink hit areas.
- **Don't disable text scaling / reduce-motion.** The app uses default Material
  transitions only, which already honour the platform settings; no custom
  animation caps scaling or ignores `MediaQuery.disableAnimations`.

**Regression guard** (mirrors the web axe pass) — `test/a11y/accessibility_test.dart`.
In a `testWidgets`, call `tester.ensureSemantics()`, pump the widget/screen, then:

```dart
await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
await expectLater(tester, meetsGuideline(textContrastGuideline));
```

plus `find.bySemanticsLabel(...)` to confirm icon buttons expose labels. Covers
the invoice list tile, KPI card, status badge, login screen, the capture action,
the approvals approve/reject affordances, the exception list tile + exception
status badge + exceptions screen (queue swipe/sheet actions), the invoice
activity timeline + invoice edit-sheet (icon-only close/clear controls labelled,
one merged announcement per timeline entry), the vendor list tile + vendor
status badge (one merged row announcement; every status colour clears contrast),
the invoice warnings panel (one merged "Severity: message" announcement per
warning; every severity tint clears contrast), the ERP status panel (error-row
contrast), and the advanced-search sheet (labelled icon-only close/date-clear
controls, tap-target + contrast).
`textContrastGuideline` is strict
(it caught the 4.38:1 and 2.55:1 muted-grey defects during this pass), so add a
contrast check when introducing new coloured text.

## Conventions

- **StatefulWidget + setState** for local state, **ChangeNotifier** for shared state
- **No code generation** — manual `fromJson` factories (keeps things simple)
- **Material 3** with `useMaterial3: true`
- **iOS + Android** — no web/desktop targets
- **Lint rules** (`analysis_options.yaml`): `prefer_single_quotes`, `require_trailing_commas`, `sort_pub_dependencies`, `always_use_package_imports`
