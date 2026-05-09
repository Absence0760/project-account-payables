"""SCIM 2.0 schemas — subset sufficient for Okta + Entra user provisioning.

RFC 7643 (core schema) + RFC 7644 (protocol). We implement urn:ietf:params:
scim:schemas:core:2.0:User and the ListResponse / Error envelope. Groups are
planned follow-up work.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_OP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


class SCIMName(BaseModel):
    givenName: str | None = None
    familyName: str | None = None
    formatted: str | None = None


class SCIMEmail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    value: str
    type: str | None = None
    primary: bool = False


class SCIMMeta(BaseModel):
    resourceType: str = "User"
    created: str | None = None
    lastModified: str | None = None
    location: str | None = None


class SCIMUser(BaseModel):
    """Outbound SCIM user resource."""

    schemas: list[str] = Field(default_factory=lambda: [USER_SCHEMA])
    id: str
    externalId: str | None = None
    userName: str
    name: SCIMName | None = None
    emails: list[SCIMEmail] = Field(default_factory=list)
    active: bool = True
    meta: SCIMMeta | None = None


class SCIMUserCreate(BaseModel):
    """Inbound POST /Users body from Okta / Entra."""

    schemas: list[str] = Field(default_factory=lambda: [USER_SCHEMA])
    userName: str
    externalId: str | None = None
    name: SCIMName | None = None
    emails: list[SCIMEmail] = Field(default_factory=list)
    active: bool = True


class SCIMPatchOp(BaseModel):
    op: str  # "add" | "replace" | "remove"
    path: str | None = None
    value: object | None = None


class SCIMPatchRequest(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: [PATCH_OP_SCHEMA])
    Operations: list[SCIMPatchOp]


class SCIMListResponse(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: [LIST_RESPONSE_SCHEMA])
    totalResults: int
    startIndex: int = 1
    itemsPerPage: int
    Resources: list[SCIMUser]


class SCIMError(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: [ERROR_SCHEMA])
    status: str
    detail: str
    scimType: str | None = None
