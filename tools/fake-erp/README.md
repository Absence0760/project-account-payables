# fake-erp

A small, deterministic, in-memory fake of the three real ERP providers the
backend integrates with, so the real adapters in
`backend/app/services/erp_adapters/` can be exercised end-to-end with no
cloud account and no credentials. One FastAPI process, three path-prefixed
surfaces:

| Prefix | Emulates | Adapter |
|---|---|---|
| `/merge/api/accounting/v1` | Merge.dev unified accounting API | `merge_dev.py` |
| `/netsuite/services/rest/record/v1` | NetSuite SuiteTalk REST (OAuth 1.0 TBA) | `netsuite.py` |
| `/d365` | Dynamics 365 Business Central OData v4 | `dynamics_365_bc.py` |

## Auth (fails 401 in each provider's error shape)

- **Merge**: any non-empty `Authorization: Bearer …` **and** a non-empty
  `X-Account-Token` header.
- **NetSuite**: an `Authorization: OAuth …` header containing
  `oauth_consumer_key`, `oauth_token` and `oauth_signature` params. The HMAC
  signature is **not** verified — shape only.
- **D365**: `POST /d365/oauth2/token` (also `/d365/oauth2/v2.0/token` and
  `/d365/{tenant}/oauth2/v2.0/token`) accepts any `client_credentials` form
  POST with non-empty `client_id`/`client_secret` and returns
  `access_token: fake-d365-token`; every OData endpoint then requires
  `Authorization: Bearer fake-d365-token`.

## Surfaces

- **Merge**: `POST /invoices` (creates `merge-inv-<n>`, status `OPEN`; response
  nests the record under `model`), `GET /invoices/{id}` (top-level `status`),
  `GET /purchase-orders` + `GET /accounts` (cursor-paginated 2 + 1 via `next`
  / `?cursor=`), `GET /account-details` (test_connection).
- **NetSuite**: `POST /vendorBill` → **204** with the new numeric id (`1001`,
  `1002`, …) in the `Location` header, status `Open`;
  `GET /vendorBill/{id}` → `{"status": {"refName": "Open"}}`;
  `GET /vendor?limit=1` (test_connection).
- **D365**: `POST …/companies({id})/purchaseInvoices` → 201 `d365-inv-<n>`
  status `Draft`; `POST …/purchaseInvoices({id})/Microsoft.NAV.post` → 204,
  flips status to `Open`; `GET …/purchaseInvoices({id})`;
  `GET …/vendors?$top=1` (test_connection). OData base is `/d365`, i.e.
  `/d365/{environment}/api/v2.0/companies({company_id})/<resource>`.

## Fixed fixtures (e2e asserts these literals — do not change)

Purchase orders (`GET /merge/api/accounting/v1/purchase-orders`):

1. `PO-FAKE-301` — vendor "Fake ERP Vendor A", total 1250.00 USD
2. `PO-FAKE-302` — vendor "Fake ERP Vendor B", total 980.50 USD
3. `PO-FAKE-303` — vendor "Fake ERP Vendor A", total 4400.00 USD

GL accounts (`GET /merge/api/accounting/v1/accounts`):

1. `6100` "Fake Office Supplies" (expense)
2. `6200` "Fake Software" (expense)
3. `6300` "Fake Consulting" (expense)

## Test hooks

- `GET /health` → `{"status": "ok"}`
- `POST /__reset` → clears all in-memory state (counters + stored invoices)
- `POST /__set-status` `{"provider": "merge"|"netsuite"|"d365", "id": "…",
  "status": "…"}` → force a stored invoice into a provider-native status
  (e.g. merge `PAID`, netsuite `paidInFull`, d365 `Paid`) to drive
  `get_invoice_status()` transitions.

## Dependencies

Direct deps live in `requirements.in`; the image installs from the generated
`requirements.txt` with `pip install --require-hashes`, so every package —
transitive ones included — is pinned by hash. Same posture as the backend
image, and the base image is digest-pinned too.

Dependabot maintains both files: it recognises a pip-compile lockfile only
when the name ends in `.txt` and matches the `.in` basename, which is why
this directory uses `requirements.txt` rather than the backend's
`requirements.lock`. To regenerate by hand after editing `requirements.in`:

```bash
uv pip compile requirements.in --universal --python-version 3.14 --generate-hashes -o requirements.txt
```

## Running

Standalone (any venv with fastapi + uvicorn):

```bash
uvicorn app:app --port 12112   # from tools/fake-erp/
```

Via compose (opt-in `erp` profile, host port **12112** → container 8080):

```bash
docker compose -f backend/docker-compose.yml --profile erp up -d fake-erp
```

Point the backend at it with the `AP_ERP_*_API_BASE` overrides in
`backend/.env.development`:

```
AP_ERP_MERGE_API_BASE=http://localhost:12112/merge/api/accounting/v1
AP_ERP_NETSUITE_API_BASE=http://localhost:12112/netsuite/services/rest/record/v1
AP_ERP_D365_API_BASE=http://localhost:12112/d365
AP_ERP_D365_TOKEN_URL=http://localhost:12112/d365/oauth2/token
```
