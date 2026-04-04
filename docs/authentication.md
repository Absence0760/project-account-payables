# Authentication

JWT-based authentication using `python-jose` for token handling and `passlib` with bcrypt for password hashing.

## Auth Flow

1. User submits email/password at `/login`
2. Frontend POSTs `{ email, password }` to `/api/auth/login`
3. Backend validates credentials and returns `{ access_token, token_type }`
4. Frontend stores the JWT in `localStorage`
5. All subsequent API requests include `Authorization: Bearer <token>` header
6. On 401 responses, the token is cleared and the user is redirected to `/login`

## Backend Implementation

### Login endpoint

`POST /api/auth/login` — accepts email and password, verifies against the hashed password in the database, and returns a signed JWT.

### Current user endpoint

`GET /api/auth/me` — returns the authenticated user's profile. Used by the frontend layout to validate the session on page load.

### Protected routes

All endpoints except `/api/auth/login` and `/api/health` require a valid Bearer token. Authentication is enforced via FastAPI dependencies in `app/api/deps.py`.

## Frontend Implementation

- Auth state is managed in `src/lib/stores/auth.svelte.ts` (Svelte 5 runes)
- API client (`src/lib/api.ts`) automatically attaches the Bearer token to every request
- The root `+layout.svelte` guards all routes — unauthenticated users are redirected to `/login`
- The login page has its own layout without the sidebar

## Configuration

| Variable        | Default                    | Description       |
|-----------------|----------------------------|-------------------|
| `AP_SECRET_KEY` | `change-me-in-production`  | JWT signing key   |

Set this to a strong, random value in production.

## RBAC (Role-Based Access Control)

The database supports four roles:
- **admin** — full access
- **ap_manager** — manage invoices and approvals
- **ap_clerk** — create and edit invoices
- **cfo** — view and approve

RBAC enforcement via FastAPI dependencies (`Depends(require_role("manager"))`) is planned for future phases.

## Testing Auth via curl

```bash
# Login and capture token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@acme.com","password":"demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Use the token
curl http://localhost:8000/api/invoices \
  -H "Authorization: Bearer $TOKEN"

# Get current user
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer $TOKEN"
```
