# Security policy

Thank you for helping keep this project and its users safe.

## Reporting a vulnerability

**Do not open a public GitHub issue for a suspected vulnerability.** Public
issues are indexed and the moment a report lands the window between
disclosure and a fix is the most dangerous one.

Instead, use GitHub's private vulnerability reporting:

1. Navigate to the [Security tab](../../security) of this repository.
2. Click **Report a vulnerability**.
3. Fill in the form. Steps-to-reproduce and a minimal proof-of-concept
   make triage faster; speculation about impact is welcome but not
   required.

If private reporting is unavailable for any reason, open a draft GitHub
discussion marked confidential, or contact the maintainers via the email
on the repo owner's GitHub profile.

We aim to acknowledge new reports within **3 business days** and to ship
a fix or coordinate a disclosure timeline within **30 days** for
high-severity issues. Lower-severity issues track the normal release
cadence.

## Scope

In scope:

- Backend (`backend/`): authentication, tenant isolation, money-moving
  paths, secret handling, audit-log integrity, webhook signature
  verification.
- Frontend (`frontend/`): cross-tenant data exposure, XSS in the
  rendered UI, JWT handling, CSP / cookie configuration.
- Mobile (`mobile/`): credential storage, deep-link handling,
  background-sync data exposure.
- Infrastructure (`infra/`): Terraform IaC misconfigurations (public
  buckets, over-broad IAM, etc.).
- Anything that could compromise SOC 2 controls listed in
  `docs/soc2-readiness.md`.

Out of scope:

- Findings that require physical access to a user's device.
- Findings that require an already-privileged role (admin, CFO) inside
  a tenant — those are scoped to "trusted operator", not "external
  attacker".
- Reports on demo data or seed credentials (`backend/scripts/seed.py`).
  These are intentionally weak; no production data is reachable through
  them.
- Bug reports unrelated to security (use issues / discussions for those).

## Supported versions

This project ships rolling releases off `main`. We do not maintain
parallel security branches. Security fixes land on `main`; downstream
consumers should track `main`.

## Coordinated disclosure

If you would like credit, include the name + handle you'd like used in
the public advisory. We default to attributing every fix in the
GitHub-generated security advisory.
