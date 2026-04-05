# User Management

## Overview

Users are managed per-organization. There is no self-registration — an admin invites users into their organization. All user and role data lives in the control-plane database, shared across tenants.

## Roles

Four roles are available. Users can have multiple roles.

| Role | Description |
|---|---|
| **Admin** | Full access to all features and user management |
| **AP Manager** | Review and approve invoices |
| **AP Clerk** | Upload invoices and enter data |
| **CFO** | Approve high-value invoices and view reports |

Roles are enforced in the frontend UI. The `/api/auth/me` endpoint returns the user's roles, and the frontend restricts visibility and actions based on them.

### Role-Based UI Restrictions

| Feature | Admin | AP Manager | AP Clerk | CFO |
|---|---|---|---|---|
| Sidebar: Invoices, Dashboard | Yes | Yes | Yes | Yes |
| Sidebar: Payments, Vendors | Yes | Yes | No | Yes |
| Sidebar: Workflows, Admin, Org | Yes | No | No | No |
| Invoice: edit fields | Yes | Yes | Yes | Yes |
| Invoice: change status dropdown | Yes | Yes | No | Yes |
| Invoice: submit for review (new) | Yes | Yes | Yes | Yes |
| Invoice: approve/reject | Yes | Yes | No | No |
| Invoice: delete | Yes | Yes | No | Yes |
| Bulk: delete, status change | Yes | Yes | No | Yes |
| Bulk: export | Yes | Yes | Yes | Yes |

Backend API endpoints are not yet role-gated — enforcement is UI-only for now.

## Invite Flow

1. An admin navigates to **Admin > Users** and clicks **+ Invite User**
2. They enter the user's **full name**, **email**, and select **roles**
3. The system generates a **12-character temporary password** (alphanumeric)
4. A confirmation dialog shows the credentials (email + temp password)
5. The admin clicks **Copy to Clipboard** and shares the credentials with the user out-of-band (Slack, email, etc.)
6. The user logs in at `<tenant>.localhost:7777/login` with their email and temp password
7. The user can change their password via the **profile popover** in the sidebar

The temporary password is shown **only once** — it is not stored or retrievable after the dialog is closed.

## User Lifecycle

```
Invited (active) --> Deactivated --> Reactivated --> ...
                         |
                         +--> Deleted (permanent)
```

- **Active**: Can log in and use the system
- **Deactivated**: Cannot log in. Account preserved for audit trail. Can be reactivated.
- **Deleted**: Permanently removed. Use for users who were created by mistake. Prefer deactivation for users who have activity in the system.

## Self-Service

Users can update their own profile via the sidebar profile popover:

- **Name**: Change display name
- **Password**: Change password (requires current password for verification)

Users cannot change their own email or roles.

## API Endpoints

### Admin Endpoints (require authentication)

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/users` | List all users in the organization |
| GET | `/api/admin/roles` | List all available roles |
| POST | `/api/admin/users` | Create a user (returns temp password) |
| PATCH | `/api/admin/users/:id` | Update user name, email, roles, active status, or reset password |
| DELETE | `/api/admin/users/:id` | Permanently delete a user (cannot delete yourself) |

### Self-Service Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/auth/me` | Get current user info |
| PATCH | `/api/auth/me` | Update own name or password |

### Request/Response Examples

**POST /api/admin/users** (Invite)

Request:
```json
{
  "email": "jane@acme.com",
  "full_name": "Jane Smith",
  "role_names": ["ap_manager", "ap_clerk"]
}
```

Response (201):
```json
{
  "id": "uuid",
  "email": "jane@acme.com",
  "full_name": "Jane Smith",
  "is_active": true,
  "roles": [
    { "id": "uuid", "name": "ap_manager", "description": "Review and approve invoices" },
    { "id": "uuid", "name": "ap_clerk", "description": "Upload invoices and enter data" }
  ],
  "created_at": "2026-04-05T...",
  "temporary_password": "aB3kLm9xPq2R"
}
```

**PATCH /api/auth/me** (Self-service password change)

Request:
```json
{
  "current_password": "old-password",
  "password": "new-password"
}
```

## Data Model

Users and roles are stored in the control-plane database (not per-tenant).

```
organizations
  |-- users (organization_id FK)
        |-- user_roles (junction) --> roles
```

### User Fields

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| email | String | Unique across all orgs |
| full_name | String | Display name |
| hashed_password | String | Bcrypt hash (nullable for SSO users) |
| is_active | Boolean | Account status |
| sso_provider | String | SSO provider name (future use) |
| sso_provider_id | String | External SSO user ID (future use) |
| organization_id | UUID | FK to organizations |
| created_at | Timestamp | Account creation time |

## Authentication

- **Method**: JWT Bearer tokens
- **Login**: `POST /api/auth/login` with email + password
- **Token lifetime**: 30 minutes (configurable via `AP_ACCESS_TOKEN_EXPIRE_MINUTES`)
- **Token revocation**: Redis-backed blocklist (tokens are blocklisted on logout)
- **Password hashing**: Bcrypt

## Future Considerations

- **Backend RBAC enforcement**: Check roles in API middleware to restrict endpoints (e.g., only admins can access `/api/admin/*`, only AP Managers can approve). Currently enforcement is UI-only.
- **Email invitations**: Send invite emails with a magic link or temp password via SMTP
- **SSO integration**: The `sso_provider` and `sso_provider_id` fields exist but are not yet implemented
- **Password reset**: Self-service password reset via email (currently requires admin to reset)
- **Force password change**: Flag to require password change on first login after invite
- **Approval thresholds**: Role-based invoice approval limits (e.g., CFO required above $10,000)
