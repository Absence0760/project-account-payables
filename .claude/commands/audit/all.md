---
description: Dispatch the project's whole audit suite — every /audit-* and /audit/* command — in parallel, across both the security and the compliance families
---

Spawn every audit available in this repo in parallel and consolidate the findings into one report.

## Audits in scope

Discover the live list first — `ls .claude/commands/audit/*.md` plus the
top-level `.claude/commands/audit-*.md` — so this stays correct as commands are
added. As of writing, two families:

**Security** (`repo-security-auditor` agent)
- Top-level: `/audit-security`, `/audit-webhooks`, `/audit-money-path`
- Per-domain: `/audit/auth`, `/audit/secrets`, `/audit/xss`, `/audit/deps`,
  `/audit/infra`, `/audit/llm-endpoint`

**Compliance** (`compliance-auditor` agent)
- `/audit/gdpr`, `/audit/data-export-completeness`,
  `/audit/account-deletion-completeness`, `/audit/third-party-data-flows`,
  `/audit/cookie-consent`, `/audit/regional-availability`,
  `/audit/accessibility`

That is ~16 agents in one sweep. For a narrower run, dispatch a single family
(say so in the argument) or invoke the targeted command directly.

## Procedure

1. **Spawn one agent per audit, in parallel.** Send all dispatches in a single
   message with multiple Agent tool calls. Use the agent each command's
   `## Delegate to` section names — `repo-security-auditor` for the security
   family, `compliance-auditor` for the compliance one, `general-purpose` for
   `/audit/deps` — and pass that section's quoted sentence as the prompt's
   first line. Each agent carries the project's map already; do not re-explain
   the stack.
2. **Consolidate findings** into one report grouped by severity
   (Critical / High / Medium / Low), then by audit area. For each finding:
   file:line, what's wrong, the audit that found it.
3. **Recommend a fix order**, but don't apply fixes without explicit
   confirmation. Critical/High findings should be flagged with "fix before
   next deploy"; Medium/Low can be batched.

## Output shape

```
# Audit report — <date>

## Critical (N)
- [audit/<area>] file:line — <one-line>
- ...

## High (N)
- ...

## Medium (N)
- ...

## Low (N)
- ...

## Clean (no findings)
- [audit/<area>] no issues

## Recommended order
1. ...
2. ...
```

## Notes

- This is read-only. Each sub-audit is read-only by default.
- The report is the deliverable; do not edit code based on findings
  without asking the user first.
- If an audit finds no issues, list it under the `## Clean` section —
  easier to spot regression on the next run.
- For narrow changes (just a webhook handler, just one route), prefer
  the targeted command directly (`/audit-webhooks`, `/audit/auth`)
  rather than the full sweep.
- Several audits deliberately overlap at the edges (`/audit/gdpr` names the
  DSAR machinery that the two completeness audits walk in depth;
  `/audit/llm-endpoint` and `/audit/third-party-data-flows` both touch the
  model providers). Deduplicate in the consolidated report — one finding, with
  the audits that surfaced it listed — rather than counting it twice.
- `/audit/third-party-data-flows` produces a corrected register table as well
  as findings; carry that artefact into the report rather than flattening it
  into bullets.
