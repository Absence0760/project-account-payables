---
description: Check the jurisdictions this platform claims to serve against the ones its tax, payment, e-invoicing and residency features actually support
---

Audit where this product can honestly be sold and used, versus where signup lets someone in.

## Goal

An AP platform is jurisdictional in a way most SaaS is not: 1099 reporting is US-only, PEPPOL is EU, the national e-invoice formats are per-country and several require live government clearance, payment rails are country-bound, and a residency pin that nothing enforces is a claim rather than a control. The failure mode is a customer who signs up, onboards their suppliers, and only then discovers the feature they bought is US-shaped.

## What to check

1. **Signup accepts everyone.** `backend/app/api/signup.py` — is there any country selection, or any gating at all? If not (the likely answer), the finding is not "add a gate"; it is *does the product tell the truth about what it can do for a non-US tenant before they commit*.
2. **Tax features by jurisdiction.** `/api/tax` is 1099 (US: W-9/W-8, EIN/SSN, Tax1099 export, the 1099-K card-rail exclusion). `/api/international-tax` is VAT/GST/withholding with per-country rules and a pluggable rate adapter. Confirm the UI does not present US-only surfaces to a tenant whose reporting currency and residency are plainly not US — and vice versa.
3. **E-invoicing coverage.** `backend/app/services/e_invoice/country_formats/` ships FatturaPA (IT), CFDI (MX), NF-e (BR) and DIAN (CO), plus generic UBL/CII. Per `docs/roadmap.md` and the e-invoicing doc, **live government clearance (SdI / SAT-PAC / SEFAZ / DIAN) is deferred per format** — so the file generates but is not filed. Confirm the product says "generates a compliant file" rather than implying clearance, because in those four countries an uncleared invoice is not a valid invoice.
4. **PEPPOL.** Send and inbound receive both exist, gated on an Access Point. Confirm the participant-id and BIS Billing 3.0 handling do not assume an EU-only tenant when the network has non-EU members, and that `FEOH_PEPPOL_INBOUND_ENABLED` being off is presented as off.
5. **Payment rails by country.** `services/payment_adapters/` — Modern Treasury, Stripe Treasury, Increase, Column, Dwolla (ACH, US-only), Checkeeper (US cheque printing) are US-centric; Nium is the international card rail. Cross-reference `INTERNATIONAL_PAYMENT_METHODS` in `services/payment_methods.py`. A UK or ZA tenant needs to know which rails are reachable *before* they configure banking — check whether anything surfaces that, or whether they discover it at the first failed payment run.
6. **Residency versus deployment.** `docs/data-residency.md`: the pin is advisory, the `alignment` block is the only signal, and `FEOH_DEPLOYED_REGION` empty means "cannot attest" (`aligned: null`). Confirm nothing in the UI or docs upgrades that to an assurance, and that an EU-pinned tenant on a US deployment sees the misalignment rather than a silent green tick.
7. **Sanctions and export control.** Screening exists for **vendors** (`sanctions_adapters`) but the question here is the **customer**: is there any check that the signing-up organisation is not in a sanctioned jurisdiction? Absence is a business finding, not a bug — report it as one, with the note that it also affects which payment providers will onboard us.
8. **Locale coverage versus market claims.** Six UI locales (de, en, es, fr, ja, pt-BR). Compare against the jurisdictions the tax/e-invoice features target — Italy and Colombia have formats but no `it`/`es-CO` nuance; Japanese is translated but there is no JP e-invoice format (Qualified Invoice / JP PINT). Neither is wrong, but a mismatch between "we speak your language" and "we support your filing" is worth naming.
9. **Provider availability.** A configured adapter that is itself region-limited (an AI provider unavailable in a country, a payment provider that will not onboard a given entity type) should be visible before a tenant depends on it.

## Report

Frame findings as **capability versus claim**, and route them accordingly:

- **High** — the product implies a regulatory capability it does not have (filing versus generating a cleared e-invoice; residency as storage location).
- **Medium** — a jurisdiction can sign up and reach a dead end with no forewarning.
- **Low** — locale/feature-coverage mismatch, undocumented provider region limit.

Most fixes here are **doc or product** rather than code — say which. Cross-check against `docs/competitive-analysis.md` and `docs/roadmap.md` before filing an unshipped feature as a gap.

## Delegate to

Use the `compliance-auditor` agent: `"Audit regional availability — signup gating, tax and e-invoicing coverage per jurisdiction including deferred government clearance, payment-rail country limits, residency claims versus the deployed region, customer-side sanctions screening, and locale versus filing coverage."`

Read-only. Findings only.
