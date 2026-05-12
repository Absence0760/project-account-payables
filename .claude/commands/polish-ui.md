---
description: Polish the UI/UX of a single page or component to project-account-payables' quality bar — workspace layout, filter chips, short date formatting, status badges, RowAction / BulkBar / SearchBox reuse. Delegates to the `ui-polisher` agent.
argument-hint: <page-route or component path>
---

Polish the UI/UX of `$ARGUMENTS` using the `ui-polisher` agent.

## When to use this command

**Right fit:**

- An index page that doesn't use the wide-screen real estate well (cramped middle, cards in a narrow grid on a 1920px display).
- A page where alignment drifts row-to-row (badges / chips / dates at different x-positions).
- A page leaking raw ISO dates or full `toLocaleString()`, missing status filter chips, inline create forms that should be modals, missing search when the list can grow, or missing load-more pagination.
- A page whose archetype doesn't match the data — flat card list when the data is workflow-state, no master/detail when each item has rich inspector content, no filter chips when the data is bucketed by status.
- A modal or component used in multiple places where consistency matters.

**Wrong fit — tell the user and stop:**

- A purely-functional Settings / login / change-password / profile / MFA page with no real-estate or scanability problem.
- A detail page (`/workflows/[id]`, etc.) that already has rich UI — polish on detail pages usually has a worse cost/value ratio than on index pages.
- A request that's really a feature, not a polish — "add a chart of invoice volume over time" needs a feature plan, not the polish agent.
- An asks-for-everything sweep ("polish all pages"). Pick one and tell the user to invoke this command again for the next.

## Resolving the target

`$ARGUMENTS` can be:

- A **route slug** (`/invoices`, `/vendors`, `/payments`, `/exceptions`, `/workflows`, `/admin`, `/purchase-orders`, `/goods-receipts`, `/credit-memos`, `/organization`, `/` for the dashboard) — resolves to `frontend/src/routes/<slug>/+page.svelte` (or `frontend/src/routes/+page.svelte` for `/`).
- A **file path** (`frontend/src/lib/components/InvoiceModal.svelte`) — used as-is.
- A **component name** (`InvoiceModal`, `RunDetailModal`, `BulkBar`) — resolve via `find frontend/src/lib/components -name "<name>.svelte"`.

If the argument is empty or "audit", list the candidate index pages with a one-line "why this one matters most right now" and ask the user to pick. Don't blanket-sweep.

## The flow

1. **Pre-flight:**
   - Confirm the frontend dev server is up at `:7777` on the tenant subdomain (`curl -s -o /dev/null -w '%{http_code}' http://acme.localhost:7777/`). If not, tell the user and stop — the agent's screenshot step needs it.
   - Confirm the backend is up at `:8000` (`curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/health`). If not, tell the user (`cd backend && docker compose up -d && python main.py`) and stop.
   - Confirm the seed has been run at least once (the screenshot uses `demo@acme.com` / `demo`). If the login fails during the screenshot, the agent will surface it.

2. **Resolve target → invoke the agent:**

   Spawn the `ui-polisher` agent with a prompt like:

   > "Polish the UI/UX of `<resolved file path>`. The user's stated intent was: `<the original argument string>`. Follow your agent spec: audit, plan, edit, verify, report. Do not commit."

   The agent's spec covers the design language, screenshot capture, type-check, and e2e selector updates. Trust it.

3. **Relay the agent's report.** When it returns, surface:

   - The before/after screenshot paths so the user can open them.
   - The list of files changed (run `git diff --stat` to confirm).
   - Any e2e selector updates the agent applied so the user can sanity-check those edits.
   - The agent's "Notes for the human" section verbatim.

4. **Wait for the user's call on the commit.** Do not pre-stage or pre-commit. When the user says yes:

   - Stage the changed files explicitly (don't `git add -A` — risk of pulling in unrelated test results / screenshots).
   - Commit message follows a `ui(<scope>):` convention. **No `Co-Authored-By` / "Generated with Claude Code" / robot-emoji footers** — the user-level rule wins.
   - Example: `git commit -m "ui(invoices): <one-liner>" -m "<3-5 line body explaining what archetype + which patterns applied>"`.

## Cost reality

This command costs more than a normal edit (a screenshot pass, full type-check, possible e2e re-run, an agent context). Don't burn it on a 5-pixel padding tweak — for that, the user edits directly. The command earns its cost on archetype-level or hierarchy-level changes (a card grid that should be a table, a flat list that should be master/detail, a missing filter-chip row, etc.).

## What this command does NOT replace

- `/check` for a pre-commit gate (code-review + test-gap + doc-hygiene).
- `/safe-edit` for security-sensitive changes.
- `/audit-security`, `/audit-money-path`, `/audit-webhooks` for periodic broad sweeps.

## Tone

Don't narrate the agent's internal steps. The user sees:

- A one-sentence "Resolving target → `<path>`. Spawning the polisher."
- The agent's structured report (audit findings + changes + verification + notes), relayed.
- A "Want me to commit?" question with the suggested commit message.
