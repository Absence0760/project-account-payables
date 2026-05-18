---
description: Sweep every backend route for auth-middleware gating + tenant-context wrapper discipline
---

Audit auth gating and tenant-context enforcement across the backend API.

## Goal

A single route registered without the auth middleware exposes whatever it does to anonymous callers. A single route that bypasses the tenant-aware query wrapper reads across tenant boundaries — RLS policies (or whatever isolation mechanism this stack uses) don't apply because the per-connection tenant marker (`SET app.current_org_id = …`, or the equivalent) was never set. Find both classes of bug in one pass.

## What to check

1. **Global route registration.** Read the entrypoint that mounts every router/handler (e.g. `backend/src/index.ts`, `app.py`, `main.go`). The default should be "auth middleware applied at the mount". Public-by-design mounts are an explicit allowlist:
   - `/health` and similar liveness/readiness probes
   - Pre-auth endpoints (`/auth/login`, `/auth/register`, `/auth/forgot-password`, `/auth/reset-password`, `/auth/verify-email`)
   - Public read endpoints intended for unauthenticated consumption (status badges, public webhooks, OAuth callbacks)

   Anything else mounted without the auth middleware is a finding. Anything mounted *with* the auth middleware but with sub-routers that re-expose a handler via a different public mount needs re-checking — the middleware applies at the mount point, not per-handler.

2. **Non-null `user` assertions in handlers.** Inside any route handler, code like `req.user!.id` (TS) / `request.user.id` (Python) / `ctx.User.OrgID` (Go) is only safe when the auth middleware actually ran upstream. Grep route handlers for these accesses and confirm each lives in a router gated by the auth middleware.

3. **Tenant-aware query wrapper discipline.** Routes that read or write tenant-scoped data must use the tenant-aware wrapper (e.g. `tenantQuery(orgId, sql, params)` / `tenant_db(org_id).query(...)` / `WithTenant(ctx, orgId, ...)`). The wrapper's job is to set the per-connection tenant marker before queries fire so RLS sees an org context. The DB connects as a **non-superuser role** so RLS is actually enforced — confirm this in the project's DB-connection docs.

   **Pre-tenant queries are legitimate** (don't flag) when they're explicitly cross-tenant by design:
   - Auth middleware itself — looking up the API key / JWT subject before tenant context can be set
   - Login / registration / invite acceptance — pre-tenant by definition
   - Background jobs / cron sweeps that iterate every org
   - Public-read endpoints (badges, OAuth handshake)
   - Admin-only DB introspection

   Any **new** raw query in a tenant-scoped route is a finding unless the file has a comment explaining why it's cross-tenant by design.

4. **Resource-level authorization on path-param handlers.** Endpoints that take a resource id in the URL (`/orders/:id`, `/runs/:id`, `/reports/:id`) must verify the resource belongs to the caller's org/user **before** doing the work:

   ```
   tenantQuery(orgId, "SELECT 1 FROM <table> WHERE id = $1", [resourceId])
   ```

   then fail closed if no row returns. The canonical gotcha is the *streaming* endpoint (SSE / WebSocket) — without the gate, an authenticated user from a different org can subscribe to another org's live events.

5. **Token in query string.** For endpoints that can't accept `Authorization: Bearer` (browser `EventSource` for SSE, `<img>` with credentials), tokens are sometimes accepted as `?token=…`. Verify the token-from-query path runs through the same JWT verification + org binding as the header path. Token-in-URL is an accepted footgun for SSE; the mitigation is short TTL + HTTPS + scoping to that single path.

6. **API key paths.** If the project supports machine-to-machine API keys, confirm:
   - Keys are hashed at rest (with a non-secret prefix retained for fast lookup, not the full hash).
   - Key lookup runs *before* tenant context is set (a legitimate pre-tenant query).
   - The resulting user/org context is established from the *stored* `api_keys.org_id`, never from a request header or body.

## Report

Group findings by severity:

- **Critical** — a route exposes tenant data to anonymous callers; a route lets one tenant read/write another tenant's rows because the tenant marker wasn't set.
- **High** — non-null user assertions reachable in a path that isn't gated; a `:resourceId` handler doesn't verify ownership.
- **Medium** — new raw query in a tenant-scoped route without a comment explaining why; a per-route auth check that duplicates (and could drift from) the global one.
- **Low** — public mount missing a comment explaining why it's public.

For each: file:line, the concrete fix, the worst-case blast radius.

## Useful starting points

- The entrypoint that mounts all routers — the full picture of who's gated.
- The auth middleware — JWT and/or API key resolution.
- The tenant-aware DB wrapper — the `set_config('app.current_org_id', …)` (or equivalent) is what closes the loop.
- Any streaming endpoint (SSE / WebSocket) — the canonical "verify the resource belongs to the caller's org" gate.
- The project's CLAUDE.md or `docs/security.md` for the documented constraint about which DB role runtime traffic uses.

## Delegate to

Use the `repo-security-auditor` agent: `"Audit auth gating and tenant-context enforcement across the backend API."`

Read-only. Findings only.
