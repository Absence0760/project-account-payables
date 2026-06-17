"""Pydantic schemas for the periodic access-review endpoints (`/api/access-reviews`)."""

from __future__ import annotations

from pydantic import BaseModel

from app.services.access_review import AccessReviewRow


class AccessReviewUser(BaseModel):
    """One reviewed user's computed elevated-access status.

    All fields are non-regulated: a name, an email, the role names held, and a
    derived dormancy verdict. No banking / tax / PAN value is ever surfaced here.
    """

    user_id: str
    full_name: str
    email: str
    roles: list[str]
    last_privileged_action_at: str | None
    dormant: bool
    days_since: int | None

    @classmethod
    def from_row(cls, row: AccessReviewRow) -> AccessReviewUser:
        return cls(
            user_id=str(row.user_id),
            full_name=row.full_name,
            email=row.email,
            roles=row.roles,
            last_privileged_action_at=(
                row.last_privileged_action_at.isoformat() if row.last_privileged_action_at else None
            ),
            dormant=row.dormant,
            days_since=row.days_since,
        )


class AccessReviewResponse(BaseModel):
    """The computed review list plus the parameters it was computed under."""

    dormant_after_days: int
    generated_at: str
    total: int
    dormant_count: int
    users: list[AccessReviewUser]


class AccessReviewAcknowledgeResponse(BaseModel):
    """Result of recording that a reviewer completed the access review."""

    acknowledged: bool
    last_completed_at: str
    reviewer_id: str
