# API Reference

FastAPI backend running on http://localhost:8000. Interactive docs available at:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

## Auth

| Method | Path              | Description                            |
|--------|-------------------|----------------------------------------|
| `POST` | `/api/auth/login` | Login with email/password, returns JWT |
| `GET`  | `/api/auth/me`    | Get current user (requires Bearer token) |

## Invoices

| Method   | Path                 | Description          |
|----------|----------------------|----------------------|
| `GET`    | `/api/invoices`      | List invoices (paginated, filterable) |
| `GET`    | `/api/invoices/{id}` | Get single invoice   |
| `POST`   | `/api/invoices`      | Create invoice       |
| `PATCH`  | `/api/invoices/{id}` | Update invoice       |
| `DELETE` | `/api/invoices/{id}` | Delete invoice       |

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

| Method   | Path                | Description          |
|----------|---------------------|----------------------|
| `GET`    | `/api/vendors`      | List vendors (paginated) |
| `GET`    | `/api/vendors/{id}` | Get single vendor    |
| `POST`   | `/api/vendors`      | Create vendor        |
| `PATCH`  | `/api/vendors/{id}` | Update vendor        |
| `DELETE` | `/api/vendors/{id}` | Delete vendor        |

## Dashboard

| Method | Path              | Description                                        |
|--------|-------------------|----------------------------------------------------|
| `GET`  | `/api/dashboard`  | Aggregated KPIs (total invoices, amount, status counts) |

## Health

| Method | Path           | Description   |
|--------|----------------|---------------|
| `GET`  | `/api/health`  | Health check  |

## Authentication

All endpoints except `/api/auth/login` and `/api/health` require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <jwt_token>
```

See [authentication.md](authentication.md) for details on obtaining a token.
