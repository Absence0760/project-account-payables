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
│   │   ├── invoice.dart         # Invoice, InvoiceStatus enum (12 states)
│   │   └── payment.dart         # Payment, PaymentMethod, DashboardData, aging, trends
│   ├── services/
│   │   ├── biometric_service.dart  # Face ID / fingerprint via local_auth
│   │   ├── camera_capture.dart     # Image picker + invoice upload
│   │   ├── offline_store.dart      # SQLite cache for offline viewing
│   │   └── push_service.dart       # Firebase Cloud Messaging + local notifications
│   ├── stores/
│   │   ├── auth_store.dart      # Auth state — login, logout, role checks
│   │   ├── invoice_store.dart   # Invoice list, filter, approve/reject (offline cached)
│   │   └── dashboard_store.dart # Dashboard KPI data (offline cached)
│   ├── screens/
│   │   ├── login_screen.dart    # Tenant + email/password login
│   │   ├── home_screen.dart     # Bottom nav host (role-aware tabs)
│   │   ├── dashboard_screen.dart # KPIs, aging, top vendors
│   │   ├── invoices_screen.dart  # Invoice list with search + status filters + camera button
│   │   ├── invoice_detail_screen.dart # Detail view with approve/reject
│   │   ├── approvals_screen.dart # Pending approvals with swipe-to-approve
│   │   ├── capture_screen.dart   # Camera/gallery capture → upload → extract
│   │   ├── payments_screen.dart  # Payment history
│   │   └── settings_screen.dart  # User profile, biometric toggle, logout
│   └── widgets/
│       ├── status_badge.dart    # Colored invoice status chip
│       ├── kpi_card.dart        # Dashboard metric card
│       └── invoice_list_tile.dart # Invoice row with vendor, amount, status
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
| Invoices | `GET /api/invoices` |
| Invoice Detail | `GET /api/invoices/{id}`, `POST /api/invoices/{id}/approve`, `POST /api/invoices/{id}/reject` |
| Approvals | `GET /api/invoices` (filtered to `ready_for_review`) |
| Payments | `GET /api/payments` |
| Settings | Uses cached auth state |

## Role-based UI

Bottom navigation adapts based on user roles (same as web frontend):

| Tab | Visible to |
|-----|-----------|
| Dashboard | All roles |
| Invoices | All roles |
| Approvals | Admin, AP Manager |
| Payments | Admin, AP Manager, CFO |
| Settings | All roles |

## Feature status

**Done:**
- Login with tenant selection
- Dashboard (KPIs, aging buckets, top vendors)
- Invoice list with search + status filter chips
- Invoice detail with approve/reject
- Approvals tab with swipe-to-approve
- Payment history list
- Role-based bottom navigation
- Settings (profile, tenant info, logout)
- JWT in secure storage (iOS Keychain / Android Keystore)
- Camera OCR — snap photo or pick from gallery → upload → trigger AI extraction
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
- Invoice editing (change fields in detail screen)
- Invoice upload via file picker (PDF/PNG/JPG/TIFF) — mobile has camera only
- PDF/image viewer for uploaded invoice files
- Activity timeline / audit log in invoice detail
- Advanced search modal (vendor, PO, amount range, date range)
- Invoice warnings/fraud flags display
- ERP status display on invoice detail
- Exception queue (list, resolve, escalate, dismiss)
- Vendor management (list, verify/reject, ERP sync)
- Payment queue (select invoices, choose method)
- Payment runs (create/execute batches)
- Payment summary cards (total paid, pending, rebates)
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
and the approvals approve/reject affordances. `textContrastGuideline` is strict
(it caught the 4.38:1 and 2.55:1 muted-grey defects during this pass), so add a
contrast check when introducing new coloured text.

## Conventions

- **StatefulWidget + setState** for local state, **ChangeNotifier** for shared state
- **No code generation** — manual `fromJson` factories (keeps things simple)
- **Material 3** with `useMaterial3: true`
- **iOS + Android** — no web/desktop targets
- **Lint rules** (`analysis_options.yaml`): `prefer_single_quotes`, `require_trailing_commas`, `sort_pub_dependencies`, `always_use_package_imports`
