# Accessibility conformance statement

**Last reviewed:** 2026-06-19
**Conformance target:** [WCAG 2.2](https://www.w3.org/TR/WCAG22/) **Level AA**
**Self-assessment status:** Partial — automated coverage in CI; manual
screen-reader audit in progress (see [Known limitations](#known-limitations)).

We are committed to making the FeohLedger accounts-payable
platform usable by everyone, including people who rely on assistive technology
(screen readers, screen magnifiers, switch access, voice control) or who
navigate without a mouse. This page is our public conformance statement; the
detailed criterion-by-criterion breakdown lives in the companion
[Accessibility Conformance Report / VPAT](./accessibility-vpat.md).

## Scope

This statement covers the three end-user surfaces of the product:

| Surface | Stack | Notes |
| ------- | ----- | ----- |
| Web app (AP staff) | SvelteKit 2 / Svelte 5, static SPA | The full invoice → approve → pay workflow, vendors, payments, exceptions, analytics, admin. |
| Supplier portal | SvelteKit (same app, `/portal/*`) | Vendor self-service: invoice submission, payment history, virtual-card reveal, supplier chat. |
| Mobile app | Flutter 3.41+ (iOS + Android) | Core approval workflow, camera OCR, push notifications. |

Marketing / signup pages (`/`, `/signup`, `/verify`) are in scope as part of
the web surface. Third-party embedded surfaces we do not control (an external
IdP's hosted SSO login, a payment processor's hosted card-capture page) are out
of scope for this statement; where we hand off to them we note it.

## How we test

Accessibility is verified at three layers — automated regression in CI plus two
kinds of human review:

1. **Automated (axe-core, every CI run).** The Playwright e2e suite includes an
   accessibility regression guard at `frontend/tests-e2e/a11y/axe.spec.ts`. It
   runs [`axe-core`](https://github.com/dequelabs/axe-core) (via
   `@axe-core/playwright`) against the key authenticated surfaces (dashboard,
   invoices list, vendors, payments, exceptions, the invoice detail modal, the
   whole `/admin` section — Users & Roles, API Keys, Webhooks, Partner Admin —
   plus `/vendor-statements` and its create modal) and the two unauthenticated
   login surfaces (AP login, supplier portal login),
   asserting **zero violations** at the WCAG 2.0 / 2.1 / 2.2 Level A + AA tag
   set (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`). It runs on
   every push as part of the normal e2e job, so a change that reintroduces a
   machine-detectable barrier (missing label, low contrast, ARIA misuse,
   broken landmark/heading structure, undersized target) fails CI. Run it
   locally with `pnpm test:e2e:a11y` (needs the local stack up — see
   `frontend/tests-e2e/README.md`).

   The `/admin` routes were added because that section holds the app's densest
   cluster of a11y-sensitive controls — modal dialogs, armed two-click
   destructive actions, and one-time secret reveals whose focus management is
   the only thing between a user and a credential they can never see again —
   and had no coverage at all. Each route in the list can carry a `ready`
   heading the spec waits on **in addition to** the sidebar: these pages fetch
   before they render, and the sidebar alone would let axe scan a loading frame
   and pass on markup no user ever sees. `/vendor-statements` is scanned as a
   list *and* with its create modal open, since the radio `fieldset`/`legend`
   intake picker, the file input and the persistent `role="alert"` refusal
   region all live in the dialog.
2. **Navigability tests (web).** `frontend/tests-e2e/a11y/screen-reader.spec.ts`
   asserts the structural semantics a screen-reader/keyboard user relies on:
   skip link + named landmarks + a single `<h1>`, no positive tabindex, 320px
   reflow with no horizontal scroll, and dialog focus-trap + focus-restore on
   Esc. `workflow-builder.spec.ts` covers the keyboard step-reorder path.
3. **Flutter semantics tests (mobile).** The mobile app uses Flutter's
   `Semantics` tree and `meetsGuideline` widget tests (`mobile/test/a11y/`) to
   assert that interactive widgets expose labels, roles, and state to TalkBack /
   VoiceOver, plus tap-target size and contrast.
4. **Manual screen-reader passes.** Keyboard-only and screen-reader walkthroughs
   of the core flows — **VoiceOver** (macOS Safari + iOS), **NVDA** (Windows
   Firefox/Chrome), **TalkBack** (Android) — run from the repeatable
   [screen-reader checklist](./accessibility-screen-reader-checklist.md) before a
   release touching the core flow, navigation, modals, or forms. Automated
   tooling catches roughly a third to a half of WCAG issues; this pass covers the
   rest — announcement quality, reading order as heard, focus-not-obscured,
   consistent help, and accessible authentication.

### What the automated guard cannot catch

axe-core (like all automated tooling) verifies machine-detectable criteria
only. It does **not** assert the inherently human-judgement criteria — focus
order quality, meaningful link text in context, error-message clarity, the
WCAG 2.2 additions 2.4.11 Focus Not Obscured / 3.2.6 Consistent Help / 3.3.7
Redundant Entry / 3.3.8 Accessible Authentication, or that a screen reader
actually announces a state change usefully. Those depend on the manual passes
above. The VPAT marks any criterion not yet covered by a completed manual pass
as **Partially Supports** and names the remaining work.

## Accessibility features

The current build implements:

- **Skip-to-content link** as the first focusable element on every page.
- **Keyboard operability** across all interactive controls — clickable table
  rows expose a real focusable control (`RowLink`) rather than a click-only
  `<tr>`, so the keyboard path and column-header semantics both survive.
- **Visible focus indicator** (`:focus-visible`) on every interactive element.
- **Focus management in dialogs** — the shared `Modal` traps focus, returns it
  to the trigger on close, and closes on `Esc`; dialogs carry `role="dialog"`
  + an `aria-label`.
- **Live-region announcements** for toasts and async status (`aria-live`).
- **Labelled form fields** — every input has an associated `<label>`;
  required fields are marked.
- **Sufficient colour contrast** in the dark theme (text and UI components meet
  the 4.5:1 / 3:1 AA thresholds), verified by the contrast rules in the axe
  guard.
- **Reduced-motion support** — animations respect
  `prefers-reduced-motion: reduce`.
- **Semantic structure** — landmarks (`header`, `nav`, `main`), a single `h1`
  per page, and ordered headings; native controls (checkbox, radio, select,
  file, range) are restyled with `appearance: none` while preserving their
  native semantics and states.
- **Consistent navigation and help** — the sidebar nav and section tabs appear
  in the same relative order across routes.

## Known limitations

We are honest about where we are not yet fully conformant:

- **Manual screen-reader audit is in progress.** Until a full VoiceOver / NVDA
  / TalkBack pass over each core flow is signed off, criteria that depend on
  it are marked **Partially Supports** in the VPAT. This is the single largest
  piece of outstanding work.
- **Workflow-builder drag-and-drop** (`/workflows/[id]`). The no-code builder
  uses native HTML5 drag-and-drop. WCAG 2.2 **2.5.7 Dragging Movements**
  requires a single-pointer (non-drag) alternative for every drag operation;
  a click-to-add / keyboard-reorder alternative is the tracked follow-up.
- **PDF invoice previews.** Uploaded invoice PDFs are third-party documents
  whose internal tagging we don't control; the extracted invoice data is
  always available as accessible HTML alongside the preview.
- **Third-party hosted pages** (external IdP SSO login, payment-processor card
  capture) are governed by those vendors' own conformance.

None of the above blocks completing the core invoice → approve → pay workflow
with a keyboard and a screen reader.

## Legal and standards context

This statement is written against the following frameworks:

- **WCAG 2.2 Level AA** — the W3C Web Content Accessibility Guidelines, our
  technical conformance target.
- **EU European Accessibility Act (EAA), Directive (EU) 2019/882** — in force
  since **28 June 2025** for products and services placed on the EU market;
  WCAG 2.x AA (via EN 301 549) is the operative technical baseline.
- **Americans with Disabilities Act (ADA), Title III** — U.S. public
  accommodations; courts treat WCAG 2.x AA as the de-facto standard.
- **Section 508 of the Rehabilitation Act** — U.S. federal procurement;
  incorporates WCAG 2.x AA by reference. The companion VPAT/ACR is provided in
  the standard format procurement teams expect.
- **EN 301 549** — the harmonised EU standard that maps WCAG onto the EAA.

## Feedback and contact

If you encounter an accessibility barrier, or need information in an alternative
format, contact us at **accessibility@jaredhoward.com**. Please include the
page or screen, what you were trying to do, and the assistive technology and
browser/OS you were using. We aim to acknowledge accessibility reports within
five business days.

## See also

- [Accessibility Conformance Report (VPAT / ACR)](./accessibility-vpat.md) —
  the criterion-by-criterion WCAG 2.2 AA edition table.
- [Screen-reader test checklist](./accessibility-screen-reader-checklist.md) —
  the repeatable manual VoiceOver / NVDA / TalkBack pass.
- `frontend/tests-e2e/a11y/axe.spec.ts` + `screen-reader.spec.ts` — the
  automated regression guards (axe + navigability/reflow/focus-trap).
- `frontend/tests-e2e/README.md` — how to run the e2e suite (incl. the a11y
  guard) locally and in CI.
