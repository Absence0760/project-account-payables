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
│   ├── main.dart                # App entry, splash, auth routing
│   ├── config.dart              # API URL, tenant slug
│   ├── api/
│   │   ├── api_client.dart      # HTTP client (JWT + X-Tenant-Slug header)
│   │   └── endpoints.dart       # Typed API methods (auth, invoices, dashboard, payments)
│   ├── models/
│   │   ├── user.dart            # User model with role helpers
│   │   ├── invoice.dart         # Invoice, InvoiceStatus enum (12 states)
│   │   └── payment.dart         # Payment, PaymentMethod, DashboardData, aging, trends
│   ├── stores/
│   │   ├── auth_store.dart      # Auth state — login, logout, role checks
│   │   ├── invoice_store.dart   # Invoice list, filter, approve/reject
│   │   └── dashboard_store.dart # Dashboard KPI data
│   ├── screens/
│   │   ├── login_screen.dart    # Tenant + email/password login
│   │   ├── home_screen.dart     # Bottom nav host (role-aware tabs)
│   │   ├── dashboard_screen.dart # KPIs, aging, top vendors
│   │   ├── invoices_screen.dart  # Invoice list with search + status filters
│   │   ├── invoice_detail_screen.dart # Detail view with approve/reject
│   │   ├── approvals_screen.dart # Pending approvals with swipe-to-approve
│   │   ├── payments_screen.dart  # Payment history
│   │   └── settings_screen.dart  # User profile, tenant info, logout
│   └── widgets/
│       ├── status_badge.dart    # Colored invoice status chip
│       ├── kpi_card.dart        # Dashboard metric card
│       └── invoice_list_tile.dart # Invoice row with vendor, amount, status
├── test/                        # Unit and widget tests
├── ios/                         # Xcode project (auto-managed by Flutter)
├── android/                     # Gradle project (auto-managed by Flutter)
├── pubspec.yaml                 # Dependencies
└── analysis_options.yaml        # Lint rules (matches project-running style)
```

## Architecture patterns

Follows the same patterns as `../project-running/apps/mobile_android/`:

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

## Conventions

- **StatefulWidget + setState** for local state, **ChangeNotifier** for shared state
- **No code generation** — manual `fromJson` factories (keeps things simple)
- **Material 3** with `useMaterial3: true`
- **iOS + Android** — no web/desktop targets
- **Same lint rules** as project-running: `prefer_single_quotes`, `require_trailing_commas`, `sort_pub_dependencies`, `always_use_package_imports`
