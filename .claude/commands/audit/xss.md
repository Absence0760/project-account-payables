---
description: Audit user-content and CMS-content rendering paths for XSS — Svelte `{@html}`, portable-text serializers, dynamic href attributes
---

Find every place user-supplied or CMS-supplied text is rendered as HTML, and verify it's either escaped (Svelte's default) or sanitised.

## Goal

The frontend is a static SvelteKit site. Most strings are rendered as text via `{value}` — safe by default. The risk surfaces are: anything using `{@html}`, portable-text renderers, dynamically constructed `href` attributes, and SVG content. CMS content (products, gallery, testimonials) is editorially controlled but still flows through the same paths — a typo in the schema or a third-party portable-text serialiser that allows raw HTML is the regression to catch.

## What to check

1. **Svelte `{@html}`.** Grep `frontend/src/` for `{@html`. For every hit, trace the source of the rendered string. If it originates from the <CMS> (CMS content) or any user input (order form notes echoed back, etc.), the rendered value must come out of a sanitiser. Static / build-time strings are fine.

2. **Portable Text / rich-text rendering.** If the project ever introduces a <CMS> rich-text renderer (custom serialisers, `@portabletext/svelte`, etc.), confirm:
   - No serialiser maps to `{@html}` without sanitisation.
   - URL-shaped marks (`link` annotations) reject `javascript:` and `data:` schemes.
   Today the frontend uses image URLs only — flag the new surface if it appears.

3. **Dynamic `href` / `src` attributes.** Grep `frontend/src/` for `href={` and `src={` inside `<a>` and `<img>`. For each, trace the source. If user/CMS-controlled, the value must be validated to reject `javascript:` / `data:` schemes. Reference: `mailto:` and same-origin paths are fine.

4. **CMS-rendered fields used in attributes.** <CMS> product / gallery / testimonial schemas have `title`, `description`, `text`, `slug` etc. fields. Grep where each lands in the DOM — text-content via `{value}` is fine; an attribute (`alt`, `title`, `aria-label`) needs no special handling but if it's an `href`/`src`, see step 3.

5. **SVG content.** SVG can carry script. Today the frontend uses <CMS> image URLs (PNG/JPG/WebP via the CDN), not SVG, but if SVG ever becomes a renderable image type, confirm:
   - <CMS>'s image CDN rejects SVG MIME on the bucket policy, OR
   - The frontend only renders `<img src="...">` (browser treats `<img>`-loaded SVG as image, no script execution), never inline `{@html svgSource}`.

6. **Email-template HTML.** Backend `email-templates.ts` builds HTML strings sent via <email-service>. Customer name, address, notes, items, and tracking info all land in HTML. Confirm every interpolation goes through an escape helper (the project uses a small one inside `email-templates.ts`); inspect any new template path that's added.

7. **The banking-details regression test.** `backend/src/__tests__/email.test.ts` has the "no banking details in automated pending-payment email" guard. This is **not strictly an XSS test** but it lives in the same area — if the diff touches email templates, confirm the guard still passes.

## Report

- **High** — user/CMS input reaches the DOM as HTML without sanitisation. Provide a payload that would prove it (e.g. `<img src=x onerror=alert(1)>` injected via a <CMS> field renders as a script-trigger).
- **Medium** — sanitisation exists but is bypassable (e.g. a portable-text serialiser config that allows raw HTML through a specific block type), or `href` validation accepts a borderline scheme.
- **Low** — escaping is correct but the surrounding code makes future XSS easy to introduce (e.g. a helper that returns a string sometimes-as-HTML, sometimes-as-text).

For each: file:line, the source of the user-supplied text, the rendering site, the missing sanitiser.

## Useful starting points

- `frontend/src/lib/Cart.svelte`, `frontend/src/routes/contact/+page.svelte` — the order-form / customer-input paths
- `frontend/src/routes/shop/+page.svelte`, `frontend/src/routes/shop/[slug]/+page.svelte` — CMS product rendering
- `frontend/src/routes/gallery/+page.svelte` — CMS gallery rendering
- `frontend/src/lib/cms.ts` — image URL builder (read-through to the <CMS>'s public CDN)
- `backend/src/email-templates.ts` — server-rendered HTML emails
- `backend/src/__tests__/email.test.ts` — the banking-details regression guard
- `docs/security.md` — search "XSS", "sanitise", "escape"

## Delegate to

Use the `repo-security-auditor` agent: `"Audit user-content and CMS-content rendering paths for XSS — Svelte {@html}, portable-text serialisers, dynamic href/src, SVG, server-rendered email HTML."`

Read-only. Report findings; don't patch without confirmation.
