# Local SSO testing with Keycloak

The app's SSO is a single generic OIDC flow (`backend/app/api/auth_sso.py`,
`backend/app/services/sso.py`) that works against any OIDC-compliant IdP —
Okta, Microsoft Entra, or anything else — because every provider-specific
detail comes from the discovery document. In production each tenant points at
their own Okta/Entra tenant. For local development you don't need a cloud
account: a **Keycloak** container is the dev-laptop equivalent of the IdP, so
you can drive the whole authorize → callback → JIT-provision flow offline.

This is the "local-first" guard rail applied to SSO: the external dependency
(a cloud IdP) ships with a local equivalent (Keycloak) and a safe default
(SSO is simply **off** until you opt in — password login always works).

## TL;DR

```bash
pnpm idp:up        # start Keycloak (Docker, opt-in `idp` profile) on :8088
pnpm idp:seed      # point the acme tenant's settings.sso at local Keycloak
pnpm dev           # backend :8000 + frontend :7777
# open http://acme.localhost:7777 → "Sign in with SSO" → demo@acme.com / demo
pnpm idp:down      # stop Keycloak when done
```

`pnpm idp:seed --slug techflow` configures a different tenant; `python
backend/scripts/enable_keycloak_sso.py --disable` (or `pnpm idp:seed` with
`--disable` appended) removes the SSO block and reverts to password-only login.

## What's provisioned

The `keycloak` service in `backend/docker-compose.yml` runs in dev mode and
re-imports `backend/keycloak/realm-export.json` on every boot, so the IdP is
always a clean, reproducible state (no persisted volume).

| Thing | Value |
|---|---|
| Keycloak admin console | http://localhost:8088 (`admin` / `admin`) |
| Realm | `account-payables` |
| Discovery URL | `http://localhost:8088/realms/account-payables/.well-known/openid-configuration` |
| Client ID | `account-payables-app` |
| Client secret | `local-dev-keycloak-secret` |
| Test user (links to seeded admin) | `demo@acme.com` / `demo` |
| Test user (JIT-provisions new) | `newhire@acme.com` / `demo` |

`pnpm idp:seed` writes exactly these values into `Organization.settings.sso`
for the chosen tenant (the same shape Okta/Entra would use), merging into the
existing settings rather than overwriting `cards` / `payments` / etc.

## Why port 8088 (not 8080)

Keycloak's own default is 8080, but that port is commonly taken by other local
dev tooling, so this repo maps the host port to **8088**. Keycloak in dev mode
derives its issuer and endpoint URLs from the request `Host` header, so hitting
it on `localhost:8088` yields an issuer of
`http://localhost:8088/realms/account-payables` with no extra hostname config —
which is what keeps the backend's discovery host check (`auth_sso.py`,
authorize-host-must-match-discovery-host) happy.

## How the two test users differ

Both demonstrate a JIT-provisioning path in `_jit_provision`:

- **`demo@acme.com`** already exists as the seeded acme admin (a password user).
  The first SSO login matches by email and *links* SSO to that existing row
  (`sso_provider` / `sso_provider_id` get set) — the user keeps their admin role.
- **`newhire@acme.com`** has no app-side row. The first SSO login *creates* one
  and assigns the least-privilege `ap_clerk` role (admins elevate later in the
  admin UI).

## Exercising the email-domain allowlist

The realm returns the standard `email`, `email_verified`, `sub`, `name`, and
`preferred_username` claims. To test the optional JIT allowlist, edit
`KEYCLOAK_SSO_CONFIG["allowed_email_domains"]` in
`backend/scripts/enable_keycloak_sso.py` to `["acme.com"]`, re-run `pnpm
idp:seed`, then try a user whose email is outside the domain — the callback
returns 403 and writes an `auth.sso.login.failure` audit row with
`reason: domain_blocked`.

## Troubleshooting

- **"SSO is not configured correctly."** on authorize — the discovery host and
  the authorize-endpoint host disagree. Usually means Keycloak was reached on a
  different host/port than what's in `settings.sso.discovery_url`. Keep both on
  `localhost:8088`.
- **Login session expired** on callback — the OIDC `state` lives in Redis with a
  10-minute TTL (`AP_SSO_STATE_TTL_SECONDS`). Make sure `pnpm db:up` (Redis) is
  running and you completed the flow within the window.
- **Keycloak slow to accept connections** — first boot pulls the image and
  imports the realm; give it ~10-15s. `pnpm idp:logs` shows progress; it's ready
  when the discovery URL returns JSON.

See also: `docs/authentication.md` § SSO, `backend/docs/docker.md`.
