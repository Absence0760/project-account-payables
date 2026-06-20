"""Schemas for user management endpoints."""

from pydantic import BaseModel, Field

from app.api.pagination import PageMeta


class RoleResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    # `is_system=True` for the four built-in roles (admin, ap_manager,
    # ap_clerk, cfo) — those gate hardcoded routes and can't be edited
    # or deleted. False for org-minted custom roles.
    is_system: bool = False
    # The role's effective granular permissions (a list of catalog strings).
    # For a system role this is its static default set; for a custom role it's
    # the stored list. Drives the /admin/roles permission checkboxes + makes a
    # custom role's grants visible.
    permissions: list[str] = []

    model_config = {"from_attributes": True}


class CreateRoleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=255)
    # Granular permissions to grant this custom role. Sanitized to the known
    # catalog server-side; an unknown / empty value grants nothing.
    permissions: list[str] = []


class UpdateRoleRequest(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    # Omitted (None) → leave permissions untouched. A provided list (even empty)
    # replaces the role's permissions. Sanitized to the catalog server-side.
    permissions: list[str] | None = Field(default=None)


class PermissionCatalogEntry(BaseModel):
    """One row of the granular-permission catalog the role editor renders."""

    key: str
    label: str


class AdminUserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    is_active: bool
    roles: list[RoleResponse]
    created_at: str

    model_config = {"from_attributes": True}


class AdminUserListResponse(PageMeta):
    items: list[AdminUserResponse]
    total: int


class CreateUserRequest(BaseModel):
    email: str = Field(..., max_length=320)
    full_name: str = Field(..., max_length=255)
    role_names: list[str] = []


class CreateUserResponse(AdminUserResponse):
    """Returned on user creation — includes the generated temporary password."""

    temporary_password: str


class UpdateUserRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    is_active: bool | None = None
    role_names: list[str] | None = None
    password: str | None = Field(default=None, min_length=6)
