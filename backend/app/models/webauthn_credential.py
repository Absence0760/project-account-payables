"""WebAuthn / passkey credentials — control-plane.

A ``WebAuthnCredential`` is a registered FIDO2 authenticator (a passkey, a
security key, a platform authenticator like Touch ID / Windows Hello) bound to a
control-plane ``User``. It is an ADDITIONAL MFA factor alongside the existing
TOTP secret on ``User.mfa_secret`` — passkeys are a separate code path
(``services/webauthn.py``), not a replacement for TOTP/email-OTP.

Placement: control-plane, keyed by ``user_id`` (which is itself keyed by org),
exactly like where ``User.mfa_secret`` lives. It is NOT a tenant-fanned table —
registered in ``tenant_provisioning.CONTROL_TABLES`` so it never lands in a
tenant DB.

A user may register MANY passkeys (phone + laptop + hardware key), so this is a
child table (one user → many credentials), unlike the single ``mfa_secret``
column. ``mfa_enabled`` on the user still gates the login challenge; having at
least one verified credential here makes ``passkey`` an offered method.

Stored material is NOT secret in the password sense — a WebAuthn public key
cannot be used to forge an assertion (only the authenticator holds the private
key). We persist:
    * ``credential_id`` — the authenticator's opaque handle (base64url), unique
      per credential, used to look the row up at assertion time. Indexed unique.
    * ``public_key`` — the COSE-encoded public key (base64url) we verify each
      assertion signature against.
    * ``sign_count`` — the authenticator's monotonic signature counter. We bump
      it on every successful assertion and reject a regression (clone-detection,
      per the WebAuthn spec). Some authenticators (most passkeys) always report
      0; a 0→0 is allowed, a decrease from a non-zero count is rejected.
None of these are PII or bank/credential material; they never go in logs.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class WebAuthnCredential(Base, TimestampMixin):
    __tablename__ = "webauthn_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # base64url-encoded authenticator credential handle. Globally unique — the
    # assertion ceremony presents this id and we resolve the row by it.
    credential_id: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    # base64url-encoded COSE public key. Verified against every assertion.
    public_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Authenticator signature counter — monotonic clone-detection guard.
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Human label so a user can tell two passkeys apart in the security UI.
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Passkey")
    # comma-joined authenticator transports (usb, nfc, ble, internal, hybrid),
    # hinted back to the browser so re-auth surfaces the right authenticator.
    transports: Mapped[str | None] = mapped_column(String(120))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
