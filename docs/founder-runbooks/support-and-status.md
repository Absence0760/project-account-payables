# Support + status page

**Why this matters**: A customer with a problem and no obvious way to
reach you cancels. A customer who sees "app is down" with no
acknowledgement thinks you've abandoned them. Neither tool is hard to
set up; neither is optional for a paying customer.

## Support channel

### What to pick

| Tool | Price (startup) | Good for |
|---|---|---|
| **Plain** | Free → $35/seat | Developer-first, clean CLI + API. No AI bloat. |
| **Linear Customer Requests** | Included with Linear | Good if you already use Linear. Minimal. |
| **Intercom** | $74/seat | Chat widget + in-app messaging. Heavier UI. |
| **Help Scout** | $25/seat | Email-only teams. Cleanest shared inbox. |
| **Front** | $19/seat | Shared inbox over email + SMS + WhatsApp. |

**Recommendation for solo / pre-seed**: Plain or Help Scout. A
dedicated email like `support@yourcompany.com` routed to the shared
inbox is fine. Skip the in-app chat widget until you have >20
customers — it's a time sink when you have 2.

### What to set up

1. **Forwarding address**: `support@yourcompany.com` forwards into
   the shared inbox.
2. **First-response SLA**: Public commitment of "within one business
   day." Deliver in <4h in practice for your first 10 customers.
3. **Knowledge base** (deferrable until customer #5): Notion,
   Document360, or the SaaS vendor's built-in KB. Five articles:
   - Getting started
   - Uploading invoices
   - Connecting your ERP
   - Payment runs explained
   - Known issues + FAQ
4. **Response templates**: Save canned replies for the top 10
   questions you'll answer every week.

## Status page

### What to pick

| Tool | Price (startup) | Notes |
|---|---|---|
| **Better Stack Status** | $29/mo | Best UX. Includes uptime monitoring. |
| **StatusPage.io (Atlassian)** | $29/mo | Industry default. Feels dated. |
| **Instatus** | $15/mo | Cheap. Clean UI. |

**Recommendation**: Better Stack. You get status page + uptime
monitoring + on-call in one product. Free tier works for pre-launch.

### What to put on it

1. **Components**: Break your product into user-visible pieces —
   "API", "Web App", "Extraction", "Payments", "Login". Each has its
   own status (operational / degraded / outage).
2. **Historical uptime**: The page computes this automatically from
   incident history.
3. **Public subscribe**: Customers can subscribe to email/SMS when
   status changes.
4. **Domain**: Host it at `status.yourcompany.com` — looks professional.

## Monitoring → status page automation

Connect Better Stack (or your monitor) to the status page so:
- Repeated health-check failures auto-post "Investigating" on the
  status page
- Recovery posts "Resolved" automatically

For our backend, point Better Stack at:
- `https://api.yourcompany.com/api/health` (every 1 min from 3 regions)
- Frontend root URL (loads the static bundle)
- Core dependency checks — if RDS or S3 is down, acknowledge it

See the
[`/api/health` endpoint](../../backend/app/api/deps.py) — it's already
wired up.

## On-call

For a solo founder, "on-call" is just "you". Minimum setup:
1. Alerts on critical monitors → SMS or phone call (Better Stack
   does this; so does PagerDuty free tier for up to 5 users).
2. Commit to responding within 15 min during business hours, 1h
   off-hours.
3. Document the 3 most likely incidents (DB down, extraction stuck,
   payments webhook broken) and their first response.

If hiring an engineer, shift them into a PagerDuty rotation week 1 —
SOC 2 expects an on-call rotation exists.

## Incident communication runbook

Don't improvise when the site is down. Write a one-page runbook:
1. **First 5 min**: Acknowledge on status page — "Investigating
   degraded API performance". Generic is fine.
2. **First 30 min**: Update status with what you know — "Database
   connection pool exhausted. Root cause identified." Don't
   speculate.
3. **Resolution**: Mark resolved on status page. Post-incident email
   to affected customers within 24h with: what happened, what we did,
   what we're doing to prevent it.

Template for the email lives in `docs/founder-runbooks/templates/`
(add one when you have a real incident to respond to — don't write
it speculatively).

## Insurance

Insurance is its own pre-customer checklist — see
[`insurance.md`](./insurance.md).

## Checklist

- [ ] `support@yourcompany.com` active, routed to shared inbox
- [ ] First-response SLA published on marketing site (or in MSA)
- [ ] Status page live at `status.yourcompany.com`
- [ ] Uptime monitoring alerts to your phone
- [ ] Incident response runbook written (one page)
- [ ] Cyber + E&O insurance quoted + bound

Time: ~1 day of clicking through SaaS signups.
Cost: ~$100/mo support + status, ~$250/mo insurance.
