---
description: Verify the consent banner gates what it claims to, and that nothing non-essential loads before consent — ePrivacy Art. 5(3) + GDPR
---

Audit the web app's cookie and third-party-script consent posture.

## Goal

ePrivacy Art. 5(3) (still in force independently of GDPR) plus GDPR's consent standard require: no non-essential cookie or third-party script before affirmative consent; granular categories rather than all-or-nothing; a "reject all" as prominent as "accept all" (multiple regulator fines for dark patterns); and a withdrawal path no harder to reach than the original opt-in.

**Start from the shape of this app, which is unusual and in our favour:** the frontend is a static, self-hosted SvelteKit bundle that ships **no analytics, no tag manager and no third-party script by default**, and it is a B2B tool people log into rather than a public marketing site. So the likely findings are not "we track people without consent" — they are **a banner that over-claims** (offering toggles for categories that do not exist, which is its own misrepresentation), **a newly added third-party embed that bypasses it**, or **consent state that does not follow the user**.

## What to check

1. **Read the banner.** `frontend/src/lib/components/ConsentBanner.svelte` and its mount point in `frontend/src/routes/+layout.svelte`. Enumerate the categories it offers and, for each, find the code it actually gates. A category with nothing behind it is a **Medium** — say so plainly rather than treating it as harmless.
2. **Page-load chain.** Walk `frontend/src/app.html`, the root `+layout.svelte`, and every `+page.ts`/`+layout.ts` for anything that fires on mount and reaches a non-first-party origin. Grep `frontend/src` and `frontend/static` for `<script src="http`, `fonts.googleapis`, `fonts.gstatic`, `cdn.`, `googletagmanager`, `analytics`, and any absolute URL in a `link`/`img`/`iframe`. Each hit is either essential (justify it) or must be consent-gated.
3. **Fonts and assets.** A self-hosted font needs no consent; a Google Fonts request leaks the visitor's IP to a third party at page load, and German courts have already ruled on exactly that. Confirm which this app does.
4. **Cookies and storage that actually exist.** Grep for `document.cookie`, `localStorage`, `sessionStorage`. Session and auth storage are **strictly necessary** and correctly exempt; the consent record itself is exempt. Anything else — a remembered filter, a dismissed-hint flag — is a judgement call worth stating rather than silently exempting. Confirm the banner's copy matches the real list.
5. **Dark-pattern check.** Reject as prominent as accept (same visual weight, not a text link beside a filled button); nothing pre-checked; the page usable without a choice, or the choice genuinely blocking — but not a fake blocker that a keyboard user can tab past.
6. **Withdrawal.** A reachable control to change or withdraw consent after the fact, no deeper than the original prompt. If there is none, that is **Medium** regardless of how little is tracked.
7. **Persistence and scope.** Where does the choice live — per browser, or on the user record? A B2B user on three devices, and a **supplier-portal** vendor who is a different data subject entirely on a different subdomain, should each get a coherent experience. Check whether the portal and the tenant subdomains share or fragment consent.
8. **The white-label surface.** Custom domains and per-tenant branding mean the banner renders under a tenant's own brand. Confirm the tenant cannot theme it into invisibility (contrast, z-index) — a banner nobody can read is not consent.
9. **Accessibility of the banner itself.** It is the first interactive element a visitor meets: keyboard-reachable, focus-trapped or not blocking, announced. Cross-reference `/audit/accessibility`.

## Report

Per finding: `file:line`, the third party or storage involved, whether it fires before consent, and the fix. Severities per the `compliance-auditor` rubric; note explicitly where the honest answer is "this app does less tracking than the banner implies", because over-claiming is the finding there.

## Delegate to

Use the `compliance-auditor` agent: `"Audit cookie + script consent — what the banner claims versus what it gates, the page-load chain for third-party origins, cookies and storage actually used, dark patterns, withdrawal, persistence across tenant subdomains and the supplier portal."`

Read-only. Findings only.
