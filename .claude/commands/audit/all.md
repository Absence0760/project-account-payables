---
description: Dispatch the project's audit suite — every /audit-* security command + /audit/auth — in parallel
---

Spawn every audit available in this repo in parallel and consolidate the findings into one report.

## Audits in scope

- **Top-level security audits** (`repo-security-auditor` agent): `/audit-security`,
  `/audit-webhooks`, `/audit-money-path`.
- **Auth + tenant-context sweep** (`repo-security-auditor` agent): `/audit/auth`.

## Procedure

1. **Spawn one agent per audit, in parallel.** Send all dispatches in a single
   message with multiple Agent tool calls. Every audit in this suite invokes
   the `repo-security-auditor` agent — pass the audit's `.md` body as the
   prompt's first sentence (e.g. `"Audit tenant isolation across HTTP routes
   and DB session resolution"` for `/audit-security`). The agent has the
   project's trust boundaries (auth, tenant isolation, money path, secrets,
   PII) baked in and routes against the area you name.
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
