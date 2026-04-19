# API Reference

FastAPI backend running on http://localhost:8000. Interactive docs available at:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

## Required Headers

### Authentication

All endpoints except `/api/auth/login`, `/api/signup/*`, `/api/public-config`, and `/api/health` require a Bearer token:

```
Authorization: Bearer <jwt_token>
```

### Tenant Routing

All business endpoints (invoices, vendors, dashboard) require a tenant slug header:

```
X-Tenant-Slug: acme
```

The frontend sends this automatically based on the subdomain. When testing via curl, include both headers.

## Auth

| Method  | Path                         | Description                                                        | Database      |
|---------|------------------------------|--------------------------------------------------------------------|---------------|
| `POST`  | `/api/auth/login`            | Login with email/password, returns JWT + `must_change_password`    | Control plane |
| `POST`  | `/api/auth/logout`           | Revoke current token via Redis blocklist                           | Control plane |
| `GET`   | `/api/auth/me`               | Get current user (roles, `must_change_password`)                   | Control plane |
| `PATCH` | `/api/auth/me`               | Update own name or password                                        | Control plane |
| `POST`  | `/api/auth/change-password`  | Set a new password (clears `must_change_password`)                 | Control plane |

Auth endpoints do **not** require the `X-Tenant-Slug` header.

## Signup (anonymous, pre-tenant)

| Method  | Path                      | Description                                                         | Database      |
|---------|---------------------------|---------------------------------------------------------------------|---------------|
| `GET`   | `/api/signup/slug-check`  | `?slug=<s>` — availability check for the signup form's inline UX     | Control plane |
| `POST`  | `/api/signup/start`       | Rate-limited + captcha-verified. Creates `email_verifications` row and sends verification email. No tenant is provisioned. | Control plane |
| `POST`  | `/api/signup/complete`    | Consumes token, provisions tenant (DB + org + admin user), generates temp password, sends welcome email.                  | Control plane → tenant DB |

See [`docs/self-service-signup.md`](../../docs/self-service-signup.md) for the full flow and abuse mitigations.

## Public config

| Method | Path                  | Description                                        |
|--------|-----------------------|----------------------------------------------------|
| `GET`  | `/api/public-config`  | Non-secret config (hcaptcha sitekey, tenant URL template) for the signup form |

No authentication required.

## Invoices

| Method   | Path                 | Description          | Database  |
|----------|----------------------|----------------------|-----------|
| `GET`    | `/api/invoices`      | List invoices (paginated, filterable) | Tenant DB |
| `GET`    | `/api/invoices/{id}` | Get single invoice   | Tenant DB |
| `POST`   | `/api/invoices`      | Create invoice       | Tenant DB |
| `PATCH`  | `/api/invoices/{id}` | Update invoice       | Tenant DB |
| `DELETE` | `/api/invoices/{id}` | Delete invoice       | Tenant DB |
| `POST`   | `/api/invoices/bulk/delete` | Bulk delete invoices | Tenant DB |
| `POST`   | `/api/invoices/bulk/status` | Bulk status change   | Tenant DB |
| `POST`   | `/api/invoices/bulk/export` | Bulk export (CSV/JSON/XML) | Tenant DB |

**Query parameters for `GET /api/invoices`:**

| Parameter       | Type   | Description                   |
|-----------------|--------|-------------------------------|
| `page`          | int    | Page number (default: 1)      |
| `page_size`     | int    | Items per page (default: 25)  |
| `status`        | string | Filter by status              |
| `vendor`        | string | Filter by vendor name         |
| `invoice_number`| string | Filter by invoice number      |
| `po_number`     | string | Filter by PO number           |
| `description`   | string | Filter by description         |
| `amount_min`    | float  | Minimum amount                |
| `amount_max`    | float  | Maximum amount                |
| `due_date_from` | date   | Due date range start          |
| `due_date_to`   | date   | Due date range end            |
| `search`        | string | Full-text search              |

## Vendors

| Method   | Path                | Description          | Database  |
|----------|---------------------|----------------------|-----------|
| `GET`    | `/api/vendors`      | List vendors (paginated) | Tenant DB |
| `GET`    | `/api/vendors/{id}` | Get single vendor    | Tenant DB |
| `POST`   | `/api/vendors`      | Create vendor        | Tenant DB |
| `PATCH`  | `/api/vendors/{id}` | Update vendor        | Tenant DB |
| `DELETE` | `/api/vendors/{id}` | Delete vendor        | Tenant DB |

## Dashboard

| Method | Path              | Description                                        | Database  |
|--------|-------------------|----------------------------------------------------|-----------|
| `GET`  | `/api/dashboard`  | Aggregated KPIs (total invoices, amount, status counts) | Tenant DB |

## Admin (User Management)

| Method   | Path                     | Description                          | Database      |
|----------|--------------------------|--------------------------------------|---------------|
| `GET`    | `/api/admin/users`       | List all users in the organization   | Control plane |
| `GET`    | `/api/admin/roles`       | List all available roles             | Control plane |
| `POST`   | `/api/admin/users`       | Create user (returns temp password)  | Control plane |
| `PATCH`  | `/api/admin/users/{id}`  | Update user (name, email, roles, password, active) | Control plane |
| `DELETE` | `/api/admin/users/{id}`  | Permanently delete a user            | Control plane |

Admin endpoints require the `X-Tenant-Slug` header (to scope user listing to the org).

## Workflow Configuration

| Method   | Path                       | Description                          | Database  |
|----------|----------------------------|--------------------------------------|-----------|
| `GET`    | `/api/workflows`           | List workflow definitions            | Tenant DB |
| `GET`    | `/api/workflows/{id}`      | Get single workflow definition       | Tenant DB |
| `POST`   | `/api/workflows`           | Create workflow definition           | Tenant DB |
| `PATCH`  | `/api/workflows/{id}`      | Update workflow definition           | Tenant DB |
| `DELETE` | `/api/workflows/{id}`      | Delete workflow (not default)        | Tenant DB |
| `GET`    | `/api/workflows/active/steps` | Get enabled steps + approval config | Tenant DB |

## Workflow Actions

| Method | Path                              | Description              | Database  |
|--------|-----------------------------------|--------------------------|-----------|
| `POST` | `/api/invoices/upload`            | Upload invoice file      | Tenant DB |
| `POST` | `/api/invoices/{id}/complete`     | Advance to next step     | Tenant DB |
| `POST` | `/api/invoices/{id}/assign`       | Assign reviewer          | Tenant DB |
| `POST` | `/api/invoices/{id}/approve`      | Approve invoice          | Tenant DB |
| `POST` | `/api/invoices/{id}/reject`       | Reject invoice           | Tenant DB |
| `POST` | `/api/invoices/{id}/resubmit`     | Resubmit after rejection | Tenant DB |
| `GET`  | `/api/invoices/{id}/audit-log`    | Full audit trail (with actor names) | Tenant DB + Control |
| `GET`  | `/api/invoices/{id}/workflow`     | Workflow instance + steps | Tenant DB |
| `GET`  | `/api/invoices/{id}/extraction`   | AI extraction results    | Tenant DB |
| `GET`  | `/api/invoices/{id}/priors`       | Priors metadata from the latest extraction (vendor cache overrides + RAG neighbors). See [`ai-extraction.md`](ai-extraction.md). | Tenant DB |
| `GET`  | `/api/invoices/{id}/export`       | Export single invoice    | Tenant DB |

## Health

| Method | Path           | Description   |
|--------|----------------|---------------|
| `GET`  | `/api/health`  | Health check  |

No authentication or tenant header required.

## Example: Full curl Flow

```bash
# Login (no tenant header needed)
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@acme.com","password":"demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# List invoices (requires both auth + tenant headers)
curl http://localhost:8000/api/invoices \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Slug: acme"

# Get dashboard KPIs
curl http://localhost:8000/api/dashboard \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Slug: acme"
```

See [authentication.md](../../docs/authentication.md) for details on the auth flow.
See [multi-tenancy.md](../../docs/multi-tenancy.md) for details on tenant routing.
