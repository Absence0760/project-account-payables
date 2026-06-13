# Email-to-invoice intake

Vendors email PDF invoices to a per-tenant address. We receive them via
an email provider webhook, create invoice rows, and dispatch extraction
— no UI interaction required. The AP team tells their vendors "send
invoices to this address" and the queue fills itself.

## Architecture

```
Vendor's mailbox
     │
     ▼ SMTP
 MX record → SES / Mailgun / Postmark
     │
     ▼ webhook (JSON / multipart)
POST /api/email-intake/inbound/{provider}
     │
     ├── HMAC signature check (rejects spoofed webhooks)
     │
     ├── Provider adapter parses → InboundEmail (normalized)
     │
     ├── Token in recipient address → resolve to Organization
     │
     ▼
 process_inbound_email(ctrl_db, payload)
     │
     ├── Open tenant session
     ├── For each PDF / image / XML attachment:
     │     ├── Create Invoice(status=pending, uploaded_by_id=NULL)
     │     ├── Upload file to S3
     │     └── Commit
     └── Dispatch extraction (one job per invoice)
```

**Accepted attachment types:** `application/pdf`, `image/png`, `image/jpeg`,
`image/tiff`, and — for structured e-invoices — `application/xml` / `text/xml`.
A UBL 2.1 or UN/CEFACT CII XML attachment is parsed deterministically by the
`einvoice` adapter (Factur-X / ZUGFeRD arrive as PDF and are covered by
`application/pdf`). See `backend/docs/e-invoicing.md`.

## Endpoints

### Public (no JWT)

`POST /api/email-intake/inbound/{provider}` — providers: `ses`, `mailgun`,
`generic`. Security is HMAC on the body (header: `X-Signature`,
`X-Webhook-Signature`, or provider-specific).

### Admin (JWT + `admin` role)

- `GET /api/organization/email-intake` — current address + enabled flag
- `POST /api/organization/email-intake/rotate-token` — invalidates the
  old address immediately and returns the new one

## What you (the founder) need to do

### 1. Pick an email provider

- **AWS SES** — Cheap, reliable, works with inbound via SNS. Requires
  domain verification (DKIM + SPF + DMARC) and receiving-rule setup.
  Best if you're already on AWS.
- **Mailgun** — Simplest onboarding, form-data webhooks. Free tier
  covers 1k emails/month, then ~$35/mo.
- **Postmark** — Clean JSON webhooks, best docs. Pair their
  inbound-email feature with the `generic` adapter (small forwarder
  Lambda). ~$15/mo for inbound.
- **SendGrid Inbound Parse** — Works but support-tax is high; avoid
  unless you already use SendGrid.

Recommended: **SES** if on AWS, **Mailgun** otherwise.

### 2. Pick and verify a domain

You need a domain where the MX records can point at the email provider.
Options:
- Subdomain of your app domain: `ap.yourcompany.com`. Keeps the primary
  domain's reputation isolated from email-intake's spam signals.
- Dedicated domain: `yourcompany-invoices.com`. More work to manage but
  easier to sunset if a provider goes sideways.

Set `AP_EMAIL_INTAKE_DOMAIN=ap.yourcompany.com` in the backend env.

### 3. Point MX at the provider

**SES:**
```
ap.yourcompany.com.  MX 10 inbound-smtp.us-east-1.amazonaws.com.
```
Then create a receipt rule in SES console → store to S3 *or* publish to
SNS. The SNS path is the one the `ses` adapter understands. Subscribe a
Lambda (or our webhook directly, via HTTPS subscription) to the SNS
topic.

**Mailgun:**
```
ap.yourcompany.com.  MX 10 mxa.mailgun.org.
ap.yourcompany.com.  MX 10 mxb.mailgun.org.
```
Then create a Route in the Mailgun console:
- Match recipient: `.*@ap.yourcompany.com` (regex route)
- Action: `forward("https://api.yourcompany.com/api/email-intake/inbound/mailgun")`
- Store and notify: unchecked (we don't need the S3 copy)

### 4. Set the signing secret

Generate a random 32-byte secret and set it both in Mailgun/SES config
*and* in the backend env:

```
openssl rand -hex 32 | tee /dev/stderr | head -c 64
# copy to both places
```

```
AP_EMAIL_INTAKE_SIGNING_SECRET=<the-hex-string>
```

For Mailgun, their built-in `signature` parameter is HMAC-SHA256 of
`timestamp + token` using your API key — **not** the same as our check.
Easiest: use the Mailgun webhook signing key as `AP_EMAIL_INTAKE_SIGNING_SECRET`
and verify via the provider's standard signature header
(`X-Mailgun-Signature-V2`).

For SES/SNS, configure the Lambda forwarder to HMAC the body before
calling our webhook.

For local dev / testing, leave the secret empty — the webhook will
accept anything (logged as a warning).

### 5. Provision tokens for each tenant

On tenant signup, call `provision_intake_token(org)` as part of the
welcome flow. For existing tenants, an admin can hit
`POST /api/organization/email-intake/rotate-token` to mint one.

The tenant's admin sees the address in Organization Settings and tells
their vendors:
> "Send invoices to `invoices+a1b2c3d4@ap.yourcompany.com`."

### 6. Tell the tenant their address is sensitive

The token is a bearer secret. Anyone who knows it can drop PDFs into
that tenant's AP queue. Rotating the token via the admin endpoint
invalidates the old address instantly.

Good practice: rotate the token annually, and immediately after a
suspected leak (e.g. a vendor forwards our intake email to someone
outside the approved recipient list).

## Local testing

No MX record needed — just POST a normalized JSON body at the `generic`
adapter:

```bash
curl -X POST http://localhost:8000/api/email-intake/inbound/generic \
  -H 'Content-Type: application/json' \
  --data '{
    "to": "invoices+<your-token>@ap.example.com",
    "from": "ap@vendor.example.com",
    "subject": "Invoice INV-1234",
    "attachments": [
      {
        "filename": "invoice.pdf",
        "content_type": "application/pdf",
        "content_base64": "'"$(base64 < sample.pdf)"'"
      }
    ]
  }'
```

Grab a token from:
```sql
SELECT slug, settings->'email_intake'->>'token'
FROM organizations;
```

The response includes the created invoice ids so you can watch them
flow through the normal extraction pipeline.

## What the intake does NOT do

- **No reply emails.** We don't acknowledge receipt to the vendor. Add a
  provider-side auto-reply if vendors need confirmation.
- **No spam filtering.** Anything hitting a valid intake address becomes
  an invoice in that tenant's queue. Lean on the provider's built-in
  spam score before our webhook.
- **No body parsing.** The email body text is stored in the invoice
  description; the extraction pipeline only looks at the attachments.
- **No sender allowlist.** If your tenant wants to restrict intake to
  specific sender domains, that's a future enhancement — the current
  model is "any email with our token gets processed".
- **No support for inline attachments.** Only `Content-Disposition:
  attachment` parts are processed. Images embedded inline in the body
  are ignored (avoids turning email signature logos into invoices).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| 401 Invalid signature | Secret mismatch between provider and backend, or the provider's signature header isn't one we check. |
| 404 Unknown email provider | Wrong URL — `{provider}` must be `ses`, `mailgun`, or `generic`. |
| 400 Could not parse provider payload | Adapter received a shape it didn't expect. Check raw body in provider logs. |
| 200 with `"error": "Unknown or disabled intake address"` | Token doesn't match any tenant, or tenant's `email_intake.enabled` is false. |
| Invoice created but stays in `pending` | Extraction worker not running / tenant ERP config broken. Same as any other upload failure — check `extraction_reaper` logs. |
| "No usable PDF / image / XML attachments" | Vendor attached `.docx` or `.zip`. Tell the vendor to send PDF, an image, or a structured e-invoice XML. |
