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
flutter gen-l10n             # regenerate AppLocalizations from lib/l10n/*.arb
```

## Project structure

```
mobile/
├── l10n.yaml                    # gen-l10n config (arb-dir, template, committed output dir)
├── lib/
│   ├── main.dart                # App entry, splash, biometric check, push init; MaterialApp.locale ← LocaleStore (i18n)
│   ├── config.dart              # API URL, tenant slug
│   ├── l10n/                    # i18n — ARB catalogues + committed gen-l10n output
│   │   ├── app_en.arb           # source-of-truth catalogue (+ app_{de,fr,es,pt,pt_BR,ja}.arb)
│   │   └── gen/                 # committed AppLocalizations (flutter gen-l10n; non-synthetic)
│   ├── api/
│   │   ├── api_client.dart      # HTTP client (JWT + X-Tenant-Slug header, timeout, debug logs)
│   │   └── endpoints.dart       # Typed API methods (auth, invoices, dashboard, payments)
│   ├── models/
│   │   ├── user.dart            # User model with role helpers
│   │   ├── audit_entry.dart     # AuditEntry + AuditFieldChange (invoice activity timeline; details.changes diff)
│   │   ├── invoice.dart         # Invoice, InvoiceStatus enum (12 states); isEditable gate mirrors backend IMMUTABLE_STATUSES
│   │   ├── mfa_challenge.dart    # MFAChallenge (login MFA-challenge response) — challengeToken + offered methods (totp/email) + mustEnroll
│   │   ├── exception.dart       # ApException, ApExceptionStatus + ApExceptionSeverity enums
│   │   ├── notification.dart    # AppNotification (in-app notification center row); eventLabel + linksToInvoice helper; copyMarkedRead for optimistic mark-read
│   │   ├── payment.dart         # Payment, PaymentMethod, DashboardData, aging, trends
│   │   ├── payment_queue.dart   # PaymentQueueItem, PaymentSummary, PaymentRun, PaymentRunSelection (money as display strings — no client float math)
│   │   ├── vendor.dart          # Vendor, VendorStatus enum (active/unverified/inactive/rejected)
│   │   └── workflow.dart        # WorkflowDefinition + WorkflowStepConfig (read-only; parses steps_config.steps; typeLabel helper)
│   ├── services/
│   │   ├── biometric_service.dart  # Face ID / fingerprint via local_auth
│   │   ├── camera_capture.dart     # Image picker (camera/gallery) + file picker (PDF/PNG/JPG/TIFF) + invoice upload
│   │   ├── file_share.dart         # Swappable share_plus wrapper — writes bytes to a temp file → platform share sheet (bulk export); FileShare.debugOverride for tests
│   │   ├── offline_store.dart      # SQLite cache for offline viewing
│   │   └── push_service.dart       # Firebase Cloud Messaging + local notifications
│   ├── stores/
│   │   ├── auth_store.dart      # Auth state — login, logout, role checks (incl. canBulkEditInvoices + isOrgAdmin gates)
│   │   ├── admin_user_store.dart # Admin user management — users + roles, set-roles / activate-deactivate (admin-only, not offline-cached)
│   │   ├── org_settings_store.dart # Organization settings — load + save the safe subset (company + invoice defaults; admin-only, not offline-cached)
│   │   ├── invoice_store.dart   # Invoice list, filter, approve/reject + multi-select bulk delete/status (offline cached)
│   │   ├── exception_store.dart # Exception list, filter, resolve/escalate/dismiss + getById (detail) + assign (in-place row patch) + multi-select state + bulkResolve (offline cached)
│   │   ├── notification_store.dart # In-app notification center — list (All/Unread filter), unread badge count, optimistic mark-read + read-all (offline cached)
│   │   ├── dashboard_store.dart # Dashboard KPI data (offline cached)
│   │   ├── cash_flow_store.dart # Predictive cash-flow forecast + cash position (CFO/admin); 30/60/90-day horizon; not offline-cached (privileged fast-moving read)
│   │   ├── locale_store.dart    # Per-device display-language choice (i18n) → MaterialApp.locale; persisted via secure storage, never account-roamed
│   │   ├── vendor_store.dart    # Vendor list, filter/search, verify/reject, ERP sync (offline cached)
│   │   ├── workflow_store.dart  # Workflow-definition list (read-only) — load + loading/error; NOT offline-cached (privileged admin read, no mutators)
│   │   └── payment_queue_store.dart # Payment queue + summary + runs; per-row method selection; create/execute/cancel runs
│   ├── screens/
│   │   ├── login_screen.dart    # Tenant + email/password login (routes to MfaScreen on an MFA challenge)
│   │   ├── mfa_screen.dart      # MFA second-factor code entry (TOTP + email-OTP backup); POST /auth/mfa/verify → JWT
│   │   ├── home_screen.dart     # Bottom nav host (role-aware tabs)
│   │   ├── dashboard_screen.dart # KPIs, aging, top vendors (app-bar: CashFlowButton + NotificationBell)
│   │   ├── cash_flow_screen.dart # Predictive cash-flow forecast (CFO/admin) — KPI summary (opening/projected-end balance, committed/pending outflow), per-period forecast + running cash-position list, low-balance alert, 30/60/90-day horizon chips, pull-to-refresh
│   │   ├── invoices_screen.dart  # Invoice list — search + status filters + advanced-search (tune) + camera + multi-select bulk delete/status (admin/ap_manager/cfo)
│   │   ├── admin_users_screen.dart # Admin — user management: list/search users, edit roles (system roles), activate/deactivate (admin-only)
│   │   ├── org_settings_screen.dart # Admin — organization settings: company profile + invoice defaults form (admin-only; ERP/payment/SSO secrets NOT surfaced)
│   │   ├── invoice_detail_screen.dart # Detail view with approve/reject + edit affordance + warnings/fraud + PO match + ERP status + activity timeline + file preview (image thumbnail / PDF card) → full viewer
│   │   ├── approvals_screen.dart # Pending approvals with swipe-to-approve
│   │   ├── exceptions_screen.dart # Exception queue — filter + swipe resolve/dismiss; tap a row → detail; long-press / checklist app-bar action → multi-select + BulkActionBar bulk-resolve (admin/ap_manager)
│   │   ├── exception_detail_screen.dart # Single-exception detail (GET /api/exceptions/{id}) — full fields + linked invoice + SLA/due/overdue + assignee, resolve/escalate/dismiss + an admin-gated assignee picker; loading/error/empty states
│   │   ├── notifications_screen.dart # In-app notification center — All/Unread filter, tap → mark read (+ deep-link to invoice detail when the row is an invoice), mark-all-read; empty/loading/error states
│   │   ├── capture_screen.dart   # Camera/gallery capture + file picker (PDF/PNG/JPG/TIFF) → upload → extract
│   │   ├── payments_screen.dart  # Payment history
│   │   ├── vendors_screen.dart   # Vendor management — list + search/status filters, swipe/sheet verify+reject (unverified only), ERP-sync app-bar action (all admin/ap_manager-gated)
│   │   ├── payment_queue_screen.dart # Pay — Queue tab (select approved invoices + per-row method → Create Run) + Runs tab (execute/cancel drafts), KPI summary bar
│   │   ├── workflows_screen.dart # Admin — read-only workflow list (name, active/default status, step count) → tap-through; reached from Settings → Administration
│   │   ├── workflow_detail_screen.dart # Read-only workflow detail — steps (number, type, name, enabled) + per-step config summary; fetches GET /api/workflows/{id} on open
│   │   └── settings_screen.dart  # User profile, biometric toggle, logout; Administration section (admin-only): User Management, Organization Settings, Workflows
│   └── widgets/
│       ├── activity_timeline.dart # Invoice audit-log timeline (action label, actor, time, per-field before→after diff); empty state; one merged Semantics label per entry
│       ├── bulk_action_bar.dart  # Bottom bar shown in invoice multi-select mode — selected count + bulk export / status-change / delete actions (each action omitted when its callback is null; reusable shape)
│       ├── advanced_search_sheet.dart # Modal bottom-sheet advanced search (vendor, PO, amount range, due-date range); seeded from live filters; min≤max + decimal validation; returns InvoiceSearchFilters (Apply) / empty (Clear) / null (dismiss)
│       ├── invoice_warnings_panel.dart # Detail-screen warnings/fraud flags (severity-coloured) + PO-match panel (match type, status, variance %, issues); one merged Semantics label per warning
│       ├── erp_status_panel.dart # Detail-screen ERP status — ErpInfo.fromAuditLog derives ERP reference / document id / send error from the audit log; shown for ERP-bound + ERP-failed statuses
│       ├── invoice_file_viewer.dart # Full-screen uploaded-file viewer — images via Image.network (auth headers), PDFs fetched as bytes via ApiClient.getBytes + rendered with pdfx; isPdf/absoluteUrl helpers; loading/error/Retry states
│       ├── invoice_edit_sheet.dart # Modal bottom-sheet edit form (vendor, invoice #, amount, PO, GL, description, due date); returns the partial diff; amount sent as string-Decimal
│       ├── status_badge.dart    # Colored invoice status chip
│       ├── exception_status_badge.dart # Colored exception status chip (open/escalated/resolved/dismissed)
│       ├── exception_list_tile.dart    # Exception row with type, invoice, severity, status
│       ├── notification_list_tile.dart # Notification row — unread dot, title, body, event label + relative time; one merged Semantics label
│       ├── notification_bell.dart      # App-bar bell action with a live unread Badge → opens NotificationsScreen (in the Dashboard app bar; visible to all roles)
│       ├── kpi_card.dart        # Dashboard metric card
│       ├── cash_flow_button.dart # Dashboard app-bar action → CashFlowScreen; gated to CFO/admin (renders nothing otherwise; mirrors backend _CFO_ROLES)
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
- **MFA**: `POST /api/auth/login` may return an **MFA challenge**
  (`{mfa_required: true, mfa_challenge_token, methods, must_enroll}`) instead of
  a `TokenResponse` when `AP_MFA_ENABLED` is on and the user is enrolled /
  org-enforced. `AuthStore.login` returns a `LoginResult`
  (`success`/`mfaRequired`/`failure`); on `mfaRequired` the login screen pushes
  `MfaScreen`, which submits the code to `POST /api/auth/mfa/verify` (`totp` or
  `email` method) → real JWT stored exactly like the no-MFA path. Email-OTP
  backup is requested via `POST /api/auth/mfa/challenge/email`. Passkey
  (WebAuthn) is web-only — never offered on mobile; MFA *enrollment* is also
  web-only (an org-enforced un-enrolled user can still verify by email, with a
  banner pointing them to the web app). Mirrors the web `/login/mfa` flow.
- Tenant: entered on login screen → sent as `X-Tenant-Slug` header
- 401 responses auto-clear session and return to login

## Screens → API mappings

| Screen | API calls |
|--------|-----------|
| Login | `POST /api/auth/login`, `GET /api/auth/me` |
| MFA (second factor) | `POST /api/auth/mfa/verify` (totp/email → JWT), `POST /api/auth/mfa/challenge/email` (request email OTP) |
| Dashboard | `GET /api/dashboard` |
| Cash Flow | `GET /api/analytics/cashflow_forecast` + `GET /api/analytics/cash_position` (both `horizon_days` + `granularity`; CFO/admin) |
| Invoices | `GET /api/invoices` (advanced search adds `vendor` / `po_number` / `amount_min` / `amount_max` / `due_date_from` / `due_date_to`); bulk ops `POST /api/invoices/bulk/delete` + `POST /api/invoices/bulk/status` + `POST /api/invoices/bulk/export` (CSV/XML → share sheet; admin/ap_manager/cfo) |
| Admin — User Management | `GET /api/admin/users` (`search`/paginated), `GET /api/admin/roles`, `POST /api/admin/users` (`email` / `full_name` / `role_names` → returns a one-time `temporary_password`), `PATCH /api/admin/users/{id}` (`role_names` / `is_active`), `DELETE /api/admin/users/{id}` — admin only |
| Admin — Organization Settings | `GET /api/organization`, `PATCH /api/organization` (`{name, settings:{company, invoice_defaults}}` — shallow-merged; admin only) |
| Admin — Workflows (read-only) | `GET /api/workflows` (list), `GET /api/workflows/{id}` (detail) — reads open to any authed role; the mobile entry point is admin-only (mirrors web nav `roles: ['admin']`). No create/edit on mobile |
| Invoice Detail | `GET /api/invoices/{id}` (carries `warnings` + `po_match`), `POST /api/invoices/{id}/approve`, `POST /api/invoices/{id}/reject`, `PATCH /api/invoices/{id}` (edit fields — admin/ap_manager/cfo, hidden in immutable statuses), `GET /api/invoices/{id}/audit-log` (activity timeline + ERP-status derivation, any authenticated role) |
| Approvals | `GET /api/invoices` (filtered to `ready_for_review`) |
| Exceptions | `GET /api/exceptions` (status filter), `POST /api/exceptions/{id}/resolve` (action=resolve\|escalate\|dismiss), `POST /api/exceptions/bulk/resolve` (`{ids, action, resolution}` → `{updated, skipped:[{id,reason}]}`) |
| Exception Detail | `GET /api/exceptions/{id}` (full row + invoice), `POST /api/exceptions/{id}/assign` (`{user_id}`, null = unassign), plus the resolve/bulk routes above. The assignee picker reuses `GET /api/admin/users` (admin-only) |
| Notifications | `GET /api/notifications` (`unread_only` filter — envelope carries `items` + total `unread`), `GET /api/notifications/unread-count` (badge), `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all` |
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

The **admin surfaces** (User Management + Organization Settings + the read-only
Workflows viewer) are not bottom-nav tabs — they live under a **Settings →
Administration** section that renders only for admins (`AuthStore.isOrgAdmin`),
mirroring the backend `require_roles(ROLE_ADMIN)` on `/api/admin/*` + `PATCH
/api/organization` and the web nav `roles: ['admin']` on `/workflows` (the
`/api/workflows` reads themselves are open to any authed role, so the Workflows
entry is a UI gate matching desktop, not a security boundary). The
**invoice bulk-ops** affordance (multi-select toggle + long-press) shows only
for `canBulkEditInvoices` (admin/ap_manager/cfo), matching the bulk endpoints'
gate; clerks never see it.

The **notification center** is not a bottom-nav tab — it's reached from the
`NotificationBell` app-bar action (with a live unread `Badge`) in the Dashboard
app bar, so it's available to **all roles** (notifications are per-user, not
role-gated; the backend scopes the list to the caller via `require_roles(*ALL_ROLES)`).

The **cash-flow forecast** is likewise not a bottom-nav tab — it's reached from
the `CashFlowButton` app-bar action (chart icon) in the Dashboard app bar,
visible only to **CFO + admin** (`AuthStore.canViewCashFlow`, mirroring the
backend `_CFO_ROLES = (admin, cfo)` gate on `/api/analytics/cashflow_forecast`
+ `/cash_position`). `ap_manager` is deliberately excluded — it's a privileged
CFO surface, not the operational dashboard. The button renders nothing for
everyone else.

## Feature status

**Done:**
- Login with tenant selection
- Dashboard (KPIs, aging buckets, top vendors). The upcoming-payments total
  (`DashboardData.upcoming.totalAmount`) reads the backend's server-computed
  `upcoming_total_amount` field directly — it is never folded from the
  `upcoming_payments` list on-device, mirroring the payment-queue and
  cash-flow "server-supplied total, never client float math" invariant
- Invoice list with search + status filter chips
- Advanced search — `AdvancedSearchSheet` (app-bar `tune` action; a dot badge marks an active advanced filter) filters the list by vendor, PO number, amount range and due-date range via `InvoiceStore.setFilters` → `GET /api/invoices` (`vendor` / `po_number` / `amount_min` / `amount_max` / `due_date_from` / `due_date_to`). Seeded from the live filters; validates min ≤ max + plain-decimal amounts; Apply / Clear / dismiss. The advanced filters compose with the quick status chips + search box (all carried into the same request + offline cache key)
- Invoice detail with approve/reject
- Invoice warnings / fraud flags + PO match — `InvoiceWarningsPanel` on the detail screen renders `Invoice.warnings` (`{type, severity, message}`, severity-coloured to WCAG AA) and the `po_match` panel (match type, status, variance %, issues). Parity with the web invoice modal; nothing renders when there are no warnings and no PO
- ERP status — `ErpStatusPanel` shows the invoice's ERP integration status (ERP reference / document id / send error + last action). `ErpInfo.fromAuditLog` derives it from the already-loaded audit log (latest `invoice.erp_*` / `invoice.completed` entry), so no extra request. Shown for ERP-bound statuses (`sending_to_erp` / `sent_to_erp` / `posted_in_erp`) and ERP-failed invoices
- Invoice editing — edit-sheet on the detail screen (vendor, invoice #, amount, PO, GL account, description, due date) via `PATCH /api/invoices/{id}`; amount sent as string-Decimal (never a lossy float); input validation; RBAC-gated (admin/ap_manager/cfo, hidden for clerks) and hidden in immutable statuses (the backend would 409); save success/failure announced via `A11y.announce`
- Activity timeline — invoice audit log on the detail screen (`GET /api/invoices/{id}/audit-log`): action label, actor, timestamp, per-field before→after diff from `details.changes`; loading / empty / error states; one merged Semantics announcement per entry
- Approvals tab with swipe-to-approve
- Exception queue (list + status filter + resolve / escalate / dismiss via swipe; admin / AP manager only). **Detail / assign / bulk-resolve** now shipped: tapping a row opens `ExceptionDetailScreen` (`GET /api/exceptions/{id}`) — full fields + linked invoice + SLA/due/overdue + current assignee, with resolve/escalate/dismiss reachable there and loading/error/empty states. An admin-gated assignee picker (`POST /api/exceptions/{id}/assign`, null = unassign) reuses the admin-only `/admin/users` list — `ap_manager` can act but doesn't get the picker (no org-user-list access); reassignment patches the row in place. Multi-select (long-press or the checklist app-bar action) drives the shared `BulkActionBar` (Status → resolve, Delete → dismiss) → `POST /api/exceptions/bulk/resolve`, whose `{updated, skipped:[{id,reason}]}` partial-success result is surfaced in a snackbar. The bottom-sheet picker is height-capped (60% of the viewport) so a long user list scrolls inside the sheet
- In-app notification center — `NotificationsScreen` + `NotificationStore` over `GET /api/notifications` (+ `unread-count` / `{id}/read` / `read-all`). Reached from the `NotificationBell` app-bar action (live unread `Badge`) in the Dashboard app bar (all roles). All / Unread filter chips; tapping a row marks it read (optimistic — flips the row + decrements the badge instantly, reconciles via refetch on failure) and deep-links to the invoice detail when the row is an `invoice` with an `entity_id` (other entity types e.g. `contract` just mark read — no mobile detail yet); mark-all-read app-bar action shown only while something is unread; offline-cached list + empty / loading / error (Retry) states. The email/in-app backend (Priority 8) serves mobile with no new endpoints
- Payment history list
- Predictive cash-flow forecast (CFO/admin) — `CashFlowScreen` + `CashFlowStore` combine `GET /api/analytics/cashflow_forecast` + `GET /api/analytics/cash_position` (same `horizon_days` + `granularity` so the two legs line up). A KPI summary (opening balance + its source, projected end balance — red when a breach is projected, total committed vs pending outflow over the horizon), a low-balance alert banner when the cash position breaches the org's persisted threshold (names the worst period + shortfall), a per-period forecast list (scheduled / committed / pending + invoice count) and a running cash-position list (period closing balance, breached rows flagged red). 30 / 60 / 90-day horizon chips (`CashFlowStore.setHorizon`), pull-to-refresh, loading / error (Retry) / empty states. Reached from the `CashFlowButton` Dashboard app-bar action (CFO/admin only). **Money is rendered from server-supplied display strings — the device never does float arithmetic on currency** (every total, opening/closing balance and shortfall is server-computed; mirrors the payment-queue invariant). Not offline-cached (privileged, fast-moving CFO read)
- Vendor management — `VendorsScreen` + `VendorStore` over `GET /api/vendors` with status filters + search; verify / reject an unverified vendor via swipe (verify ⟶ / reject ⟵) or the action sheet, and an ERP-sync app-bar action (`POST /api/vendors/sync-erp`). Read is admin/ap_manager/cfo; the mutating actions are gated to admin/ap_manager (mirrors `require_roles`) and simply hidden for CFO. Offline-cached list
- Payment queue + runs — `PaymentQueueScreen` + `PaymentQueueStore`. Queue tab lists approved invoices (`GET /api/payments/queue`), each row a checkbox + per-row method picker; the selection creates a draft run (`POST /api/payments/runs`). Runs tab lists runs (`GET /api/payments/runs/`) and executes / cancels drafts. A KPI summary bar (total paid / pending / queue / card rebates) sits above both (`GET /api/payments/summary`). CFO-approval-required runs surface the gate before an execute attempt. Money is rendered as server-supplied display strings — the device never does float arithmetic on money (totals are server-computed)
- Role-based bottom navigation
- Settings (profile, tenant info, logout)
- JWT in secure storage (iOS Keychain / Android Keystore)
- MFA challenge login — when `POST /api/auth/login` returns an MFA challenge
  (instead of a `TokenResponse`), `AuthStore.login` reports `mfaRequired` and the
  login screen routes to `MfaScreen`. The user enters their TOTP code (or
  switches to the email-OTP backup, auto-/re-requested via
  `POST /api/auth/mfa/challenge/email`), which is verified at
  `POST /api/auth/mfa/verify`; the returned JWT is stored exactly like the
  no-MFA path. Wrong/expired codes surface a friendly live-region-announced
  error and keep the user on the screen to retry. An org-enforced un-enrolled
  user (`must_enroll`) gets an email-only flow plus a banner to finish
  authenticator setup in the web app (enrollment + passkeys are web-only)
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
- **MFA enrollment** — the *challenge / verify* flow is shipped (see Done →
  "MFA challenge login"), but **enrolling** a TOTP authenticator (and managing
  passkeys) is still web-only (`/profile`). An org-enforced un-enrolled user can
  log in by email OTP on mobile and is pointed to the web app to finish setup.
- **Passkey (WebAuthn) MFA** — web-only; never offered as a mobile factor.
- **Org Security settings** — the web `/organization` page exposes the `mfa.required` toggle; mobile has no equivalent.
- **OIDC SSO** — `Sign in with Okta/Microsoft` button is web-only.
- **Workflow management (create / edit / no-code builder)** — the read-only
  list + step viewer is now on mobile (see Done → "Workflow management
  (read-only)"); creating, editing, version history, simulation and import/export
  stay desktop-only (lower value on a phone).

**Admin parity (now shipped on mobile):**
- **Bulk operations** — invoice multi-select (long-press or the checklist
  app-bar action) + bulk delete / bulk status-change / **bulk export** over
  `POST /api/invoices/bulk/{delete,status,export}`; gated to
  admin/ap_manager/cfo; the backend skips immutable-status rows and the result
  snackbar reports deleted/updated + skipped counts. **Export** offers CSV / XML
  from a format sheet, POSTs the selected ids to `bulk/export` (raw bytes via
  `ApiClient.postBytes`, which parses the `Content-Disposition` filename), writes
  the bytes to a temp file and hands them to the platform share sheet
  (`share_plus` via the swappable `services/file_share.dart`). Export is a
  non-mutating read, so it leaves the selection intact; loading + error +
  share-cancel states are announced via `A11y.announce`.
- **Workflow management (read-only)** — `WorkflowsScreen` + `WorkflowStore` over
  `GET /api/workflows` list a tenant's workflow definitions (name, active/default
  status badges, step count); tapping a row opens `WorkflowDetailScreen`
  (`GET /api/workflows/{id}`) with the configured steps (number, type, name,
  enabled flag, a short PII-free per-step config summary). Reached from Settings →
  Administration, admin-gated (`AuthStore.canViewWorkflows`, mirroring the web
  nav `roles: ['admin']`). The no-code builder — create / edit / versions /
  simulate / import-export — stays on the web; mobile is a viewer. Not
  offline-cached (privileged admin read).
- **Admin user management** — `AdminUsersScreen` over `/api/admin/*`: list/search
  users, **create a user** (a FAB opens a validated form sheet — full name +
  email + system-role pick → `POST /api/admin/users`; the server-generated
  one-time temporary password is surfaced in a dialog for the admin to hand
  over, then the list refreshes), edit a user's roles (system roles only —
  custom roles confer no access today), activate/deactivate, and **delete a
  user** (an armed/confirmed destructive action in the per-user sheet →
  `DELETE /api/admin/users/{id}`; self-delete is disabled client-side and the
  backend's 409 — self / still-referenced-by-in-flight-work — surfaces in the
  failure snackbar). Admin-only; reached from Settings → Administration.
- **Organization settings** — `OrgSettingsScreen` reads + edits the safe subset
  the web app exposes (company profile + invoice defaults) via `GET/PATCH
  /api/organization`. ERP credentials, payment/webhook secrets, extraction keys
  and SSO are deliberately NOT surfaced. Admin-only; the company `logo_url` set
  on web is carried through unedited so a save doesn't drop it.

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
status badge + exceptions screen (queue swipe actions) + the exception list tile
in **selection mode** (exposes a `selected`/checked state, keeps its tap target,
still announces one merged row label) + the `ExceptionDetailScreen` (the loaded
detail meets tap-target/label/contrast), the invoice
activity timeline + invoice edit-sheet (icon-only close/clear controls labelled,
one merged announcement per timeline entry), the vendor list tile + vendor
status badge (one merged row announcement; every status colour clears contrast),
the invoice warnings panel (one merged "Severity: message" announcement per
warning; every severity tint clears contrast), the ERP status panel (error-row
contrast), the advanced-search sheet (labelled icon-only close/date-clear
controls, tap-target + contrast), and the notification center (the
`NotificationListTile` — one merged "Unread, <event>, <title>, …" announcement
that also clears contrast on a read row; the `NotificationBell` — accessible
label carrying the live unread count, e.g. "Notifications, 3 unread"; the
`NotificationsScreen` — labelled mark-all-read action + tap-target/contrast),
and the cash-flow forecast (the `CashFlowScreen` with a breached period — the
low-balance alert exposes one merged "Low balance alert …" announcement, and the
red projected-end / breached-closing money + alert copy all clear contrast at
AA via `.shade900`), the invoice list tile in **selection mode** (exposes a
`checked` state + keeps its tap target) and the `BulkActionBar` (labelled count +
the export / status / delete actions, contrast), the read-only `WorkflowsScreen`
(loaded list meets tap-target + label + contrast; the inactive row merges into
one announcement carrying "Inactive" so the status badge isn't an unlabelled
colour cue), and the two admin screens — `AdminUsersScreen` (a
deactivated row merges into one announcement carrying "inactive" so the Inactive
badge isn't an unlabelled colour cue; the in-app-bar Material `SearchBar` is a
24px framework field exempt from the whole-screen tap-target sweep, same as the
invoices/vendors screens) and `OrgSettingsScreen` (form fields meet tap-target +
label + contrast).
`textContrastGuideline` is strict
(it caught the 4.38:1 and 2.55:1 muted-grey defects during this pass), so add a
contrast check when introducing new coloured text.

## Internationalization (i18n)

Multi-language UI via the **idiomatic Flutter `gen-l10n` + `intl` + `.arb`**
path — the mobile counterpart of the web `frontend/src/lib/i18n/` runtime.
Same six locales (en, de, fr, es, pt-BR, ja) and the same ICU plural shapes.

- **Catalogues** — `lib/l10n/app_<locale>.arb`. `app_en.arb` is the
  source-of-truth template (with `@key` placeholder metadata); the others
  translate its values. ICU plurals (`{count, plural, one {…} other {…}}`) and
  `{placeholder}` substitution are used where the web uses them. Japanese has
  no grammatical plural, so its plural blocks carry only an `other` arm.
  `app_pt.arb` is a base-Portuguese fallback that gen-l10n **requires**
  alongside the script/country `app_pt_BR.arb` (same content).
- **Config** — `l10n.yaml` drives generation; `flutter: generate: true` in
  `pubspec.yaml` wires it into the build. Output is **committed**
  (non-synthetic): `flutter gen-l10n` writes `AppLocalizations` into
  `lib/l10n/gen/` (in the tree, reviewable, travels with a clone — recent
  Flutter dropped the `synthetic-package` flag; an explicit `output-dir` is
  what lands it in the tree). **Re-run `flutter gen-l10n` after editing any
  ARB** and commit the regenerated `gen/` files.
- **Reading strings** — `AppLocalizations.of(context).<key>` (e.g.
  `l.navInvoices`, `l.dashboardInvoiceCount(n)`). A widget under test needs
  `localizationsDelegates: AppLocalizations.localizationsDelegates` +
  `supportedLocales: AppLocalizations.supportedLocales` on its `MaterialApp`
  (no explicit `locale` → defaults to `en`, so English assertions hold).
- **Per-device locale** — language is a **device** choice (like the biometric
  toggle), NOT account-roamed. `LocaleStore` (`stores/locale_store.dart`, a
  `ChangeNotifier` singleton) persists the choice locally via
  `flutter_secure_storage` and never sends it to the backend. `main.dart`
  wraps `MaterialApp` in a `ListenableBuilder` on `LocaleStore.instance` and
  passes `locale: LocaleStore.instance.locale` (`null` = follow system) so a
  picker change re-localizes the whole tree live. The picker lives in
  **Settings** (`settings_screen.dart`) — endonyms (English / Deutsch /
  Français / Español / Português (Brasil) / 日本語) plus a "System default"
  entry that clears the override.
- **String coverage** — extracted so far: the nav (`home_screen`),
  **dashboard**, **invoices list**, the **settings** screen (incl. the picker),
  and — added incrementally — the **notifications center**
  (`notifications_screen`), **vendors** (`vendors_screen`), **exceptions**
  (`exceptions_screen`), **payments history** (`payments_screen`, incl. its
  inline payment-status labels), the **approvals** screen (`approvals_screen`,
  incl. the swipe action labels + pending-count plural), the **capture** screen
  (`capture_screen`, incl. the source pickers + upload error messages), the
  **advanced-search sheet** (`advanced_search_sheet`, incl. its field labels +
  validation messages + date-field a11y hints), the **invoice detail** screen
  (`invoice_detail_screen` + its sub-widgets `invoice_warnings_panel`,
  `erp_status_panel`, `invoice_edit_sheet`, `invoice_file_viewer`,
  `activity_timeline` — detail fields, edit sheet, warnings/PO-match labels +
  severity + variance placeholder, ERP-status panel labels, file-viewer
  titles/errors, the timeline empty state), and the **payment queue/runs**
  screen (`payment_queue_screen` — Pay tabs, KPI summary bar, per-row method
  dropdown via a localized `_methodLabel`, the selected-count + payments-count
  plurals, create/execute/cancel controls + confirm dialogs, run-status chip via
  a localized `_runStatusLabel`, and the CFO-approval gate message), the **login**
  + **MFA** screens (`login_screen` — brand/tagline, tenant/email/password fields,
  show/hide-password labels, validation + Sign In; `mfa_screen` — title/heading,
  email vs TOTP prompts, org-enforcement notice, code field + validation, Verify,
  the email-OTP send/resend affordances + the emailed-code live-region
  announcement — resolved before the async gap, honouring the initState gotcha),
  the two **admin** screens (`admin_users_screen` — search hint, action sheet
  (edit-roles / activate-deactivate + hints), the role-editor, the Inactive badge
  + row a11y label, and the role-update / activate / deactivate toast placeholders;
  `org_settings_screen` — Company + Invoice-defaults section headers + every field
  label, the required-field validator placeholder, Save changes / Saving, and the
  saved / save-failed toasts), and the read-only **workflows** screens
  (`workflows_screen` — title, empty/error states, the `Active`/`Inactive`
  `WorkflowStatusBadge` (shared with the detail screen), the `Default` pill, and
  the `workflowsStepCount` plural; `workflow_detail_screen` — fallback title,
  load-error/no-steps states, default-workflow note, per-step
  number/enabled/disabled labels + the approver-count plural and delay/condition
  config-summary placeholders), the **cash-flow** screen (`cash_flow_screen` —
  title, error prefix + horizon-days placeholders, the low-balance alert
  (single/multiple breach messages), KPI labels (opening/projected-end balance +
  subtitle, committed/pending out + subtitles), opening-balance source labels,
  the projected-outflows + cash-position section headers + their empty states,
  the invoice-count plural and committed/pending/out amount placeholders, and the
  forecast/position row a11y labels + below-threshold suffix), the **contracts**
  screens (`contracts_screen` — title, search hint, empty state, status-filter
  chips; `contract_detail_screen` — title, error prefix, untitled fallback, every
  field label + the not-to-exceed/yes/no/renewal-term/notice placeholders, the
  Spend + Line-Items section headers + spend summary (invoiced/over-limit/
  remaining/of-limit/no-limit) + line-item qty/unit-price/GL placeholders, and the
  activate/terminate lifecycle controls + their confirm dialog and
  success/failure toasts), and the **exception detail** screen
  (`exception_detail_screen` — title, not-found/overdue, the description/invoice/
  SLA/assignee/resolution section headers + every field label, on-track/resolved-in
  (hours placeholder)/no-SLA states, the assign/reassign/unassign controls + the
  assignee-picker (assign-to/load-users-failed placeholder), and the resolve/
  escalate/dismiss action toasts + their failure variants). Mirrors what the web
  extracted. Data-driven
  enum/status maps that live in shared badge widgets (`status_badge`,
  `vendor_status_badge`, `exception_status_badge`) plus the `WorkflowStepConfig`
  `typeLabel` are left English until their own turn. To extract a
  string: add a flat camelCase key + value to `app_en.arb`, translate it in
  every other ARB, run `flutter gen-l10n`, and replace the literal with
  `l.<key>`. Any test that pumps a now-localized screen must wrap it in a
  `MaterialApp` carrying `AppLocalizations.localizationsDelegates` +
  `supportedLocales` (the per-file `_localized` / `_host` helper) or
  `AppLocalizations.of(context)` null-checks at runtime.
- **Tests** — `test/l10n/arb_parity_test.dart` is the ARB key-parity guard
  (mirrors the web `messages_parity`): every locale ARB has exactly the
  template's key set, no empty values, and the same placeholder *set* (deduped
  — a 1-arm `ja` plural compares equal to a 2-arm `en` one).
  `test/l10n/locale_switch_test.dart` proves the `LocaleStore` →
  `MaterialApp.locale` plumbing re-localizes a visible string live, and carries
  a per-batch guard asserting each extraction round's new keys switch with the
  locale (the notifications/vendors/exceptions/payments batch, the
  approvals/capture/advanced-search batch, the invoice-detail batch
  (detail/edit/warnings/ERP/file-viewer, incl. the `invoiceDetailErrorPrefix` +
  `warningsVarianceLabel` placeholders), the payment-queue batch (Pay
  tabs/summary/runs, incl. the `paySelectedCount` plural + `payRunExecuteFailed`
  placeholder), and the login / admin / org-settings / workflows batch (incl. the
  `workflowsStepCount` plural + `orgSettingsSaveFailed` / `adminUsersActivated` /
  `workflowDetailStepNumber` placeholders)). When a screen or shared sub-widget is
  localized, every existing
  widget/screen test that pumps it must wrap the subject in a `MaterialApp`
  carrying `AppLocalizations.localizationsDelegates` + `supportedLocales` (a
  `_localized` / `_host` / `_screenHost` helper), or `AppLocalizations.of`
  returns null at runtime. Do NOT call `AppLocalizations.of(context)` from a
  widget's `initState` (the `Localizations` inherited widget isn't available
  yet — `dependOnInheritedWidgetOfExactType … before initState() completed`);
  resolve a flag in `initState` and localize the message in `build`, as
  `invoice_file_viewer` does for its PDF-load error.

## Conventions

- **StatefulWidget + setState** for local state, **ChangeNotifier** for shared state
- **No code generation** — manual `fromJson` factories for models (the only
  generated code is the gen-l10n `AppLocalizations` under `lib/l10n/gen/`)
- **Material 3** with `useMaterial3: true`
- **iOS + Android** — no web/desktop targets
- **Lint rules** (`analysis_options.yaml`): `prefer_single_quotes`, `require_trailing_commas`, `sort_pub_dependencies`, `always_use_package_imports`
