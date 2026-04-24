# Legal entity + contracts

**Why this matters**: You cannot legally invoice a customer until you
exist as a business. You cannot accept payments into a personal bank
account without material tax pain. You cannot sign a SOC 2 auditor
engagement letter as a natural person.

## Step 1 — Incorporate

Default choice for a US SaaS: **Delaware C-corp**, formed through
Clerky or Stripe Atlas.

| Option | Cost | Time | Notes |
|---|---|---|---|
| Clerky (standard package) | $500 + DE fees | 1–2 weeks | Cleanest for fundraising. Includes 83(b), stock issuance, bylaws. |
| Stripe Atlas | $500 one-time | 1 week | Faster, simpler, includes an FDIC bank account + EIN. Slightly fewer legal docs than Clerky. |
| Firstbase | $399 | 1–2 weeks | Similar to Atlas. |
| DIY via DE Division of Corps | ~$200 | 1–4 weeks | Don't. The $300 you save costs days of admin pain. |

**If you're solo and not fundraising soon**: Atlas is faster to launch.
You can migrate to Clerky later.

**If you plan to raise in the next 6 months**: Clerky from day one.

## Step 2 — EIN, bank account, and tax

Atlas/Clerky both obtain the EIN for you. You'll also need:

- **Business bank account** — Mercury is the default startup choice
  (no minimums, clean API, SOC 2-compliant). Atlas bundles one.
- **State registrations** — Register to do business in your home state
  if different from Delaware. Clerky's add-on handles this for ~$200.
- **Sales tax nexus review** — SaaS sales tax rules vary by state. If
  you have customers in multiple states, use TaxJar or Anrok. Not
  urgent for pilot #1; do it before customer #3.

## Step 3 — Core contract library

You need these before your first customer signs:

| Document | Source | Notes |
|---|---|---|
| Terms of Service | [Termly](https://termly.io) / [Common Paper](https://commonpaper.com) | B2B SaaS template; have a lawyer review before signing with a $50K+ ARR customer. |
| Privacy Policy | Termly / Common Paper | Must include GDPR + CCPA sections even for US-only customers. |
| Data Processing Agreement (DPA) | Common Paper's [Cloud Service Agreement DPA](https://commonpaper.com/dpa/) | Every enterprise customer will ask for this. Sign their DPA or provide yours. |
| Master Services Agreement (MSA) | Common Paper's [Cloud Service Agreement](https://commonpaper.com/cloud-service-agreement/) | The customer-facing commercial contract. Standard terms, negotiable attachments. |
| Order Form / SOW | Template inside Common Paper | Per-customer commercial details — price, users, term. |
| Mutual NDA | Common Paper's mNDA | For conversations that precede the MSA. |

Common Paper is free. Use it. Rewriting B2B SaaS contracts from scratch
is a $15K legal bill with zero differentiation.

## Step 4 — Bind review with a lawyer

Before the first contract you sign for > $25K ARR, pay a startup lawyer
$2–5K to review:
- Your TOS + Privacy + DPA
- The customer's redline of your MSA
- Liability caps + indemnification clauses

Startup-friendly firms: Cooley GO, Fenwick, Wilson Sonsini, Gunderson.
Most will do flat-fee first-round reviews.

## Step 5 — Founder paperwork

Don't skip these — they have one-way consequences.

- **83(b) election** — File within 30 days of receiving founder
  stock. Clerky reminds you; Atlas sometimes doesn't. Missing this
  costs you a 6-figure tax bill when you exit.
- **Founder stock vesting** — 4-year vest, 1-year cliff. Sets
  expectations if a co-founder ever joins.
- **Operating agreement / bylaws** — Clerky/Atlas generate them.

## Checklist

- [ ] Entity incorporated (DE C-corp)
- [ ] EIN issued
- [ ] Business bank account opened
- [ ] 83(b) filed
- [ ] TOS + Privacy + DPA published on the marketing site
- [ ] MSA + Order Form templates ready to send
- [ ] Startup lawyer on retainer (or flat-fee relationship)

Total cost: ~$500–1500 one-time, plus the eventual lawyer retainer.
Total time: 1–3 weeks.
