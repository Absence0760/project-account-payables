# mobile/

Flutter app — iOS + Android client for the accounts-payable backend.

## Quick start

```bash
flutter pub get        # install dependencies
flutter run            # launch on a connected device or simulator
```

The app expects the backend on `http://localhost:8000` and a tenant slug entered on the login screen (use one of the seeded tenants, e.g. `acme` / `demo@acme.com` / `demo`). To point at a different host, edit `lib/config.dart`.

## Common commands

```bash
flutter analyze              # lint
flutter test                 # unit + widget tests
flutter build ios            # production iOS build
flutter build apk            # production Android APK
flutter build appbundle      # production Android App Bundle (Play Store)
```

## What's in here

See [`mobile/CLAUDE.md`](CLAUDE.md) for the full layout — screens, stores, API client, role-based navigation, mobile-only features (camera OCR, push, biometrics, offline mode), and the parity matrix with the web frontend.
