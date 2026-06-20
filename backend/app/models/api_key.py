"""Programmatic API keys — control-plane.

An ``ApiKey`` authenticates an org's *programmatic* (machine-to-machine)
access to the public ``/api/v1`` surface, the way a ``User`` authenticates an
interactive session. It lives in the control-plane DB keyed by ``org_id`` (NOT
a tenant-fanned table): the key resolves to its organization, and the org
resolves to its tenant DB via the existing tenant-resolution chokepoint
(``get_tenant_engine(org.db_name)``). The key itself IS the tenant boundary for
programmatic callers — there is no ``X-Tenant-Slug`` header on the v1 surface.

Hashing rationale (read before "fixing" this to bcrypt):
    API keys are high-entropy random tokens (``secrets.token_urlsafe(32)``),
    not user-chosen passwords. They must be looked up by the value the caller
    presents, and a salted bcrypt hash (the ``bcrypt_sha256`` password context
    in ``app/utils/passwords.py``) is deliberately un-indexable — you'd have to
    scan every row and bcrypt-verify each one. So we store an UNSALTED
    ``sha256(full_key)`` hex digest plus an indexed ``key_prefix`` for an
    O(log n) lookup, then constant-time-compare the digest. This is the same
    pattern the SCIM bearer token already uses (``Organization.scim_bearer_hash``,
    ``services/sso.generate_scim_token``). The ``bcrypt_sha256`` invariant in
    the project rails is the *password* path; brute-forcing a 256-bit random
    token is infeasible, so the un-salted SHA-256 is appropriate here.

The plaintext key is shown to the admin EXACTLY ONCE at mint time (in the mint
response) and is never stored or logged anywhere — only the digest + prefix
persist.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    # Human label so an admin can tell two keys apart in the management UI.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # First segment of the plaintext key (e.g. "ap_live_3kPq9xZ"). Indexed so
    # the auth path can resolve the candidate row(s) cheaply, then verify the
    # full sha256 digest in constant time. NOT a secret on its own.
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # sha256(full_key) hex digest — 64 hex chars. The only persisted form of
    # the key material. See the module docstring for why this is SHA-256, not
    # bcrypt.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Granted scopes. Start with ["read"] only this slice; the column is JSONB
    # so write/admin scopes can be added later without a migration.
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=lambda: ["read"])
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Soft-revoke: a non-null timestamp means the key is dead. We never delete
    # the row so the prefix + audit history stay resolvable.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
