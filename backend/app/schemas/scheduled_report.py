"""Pydantic schemas for scheduled-report CRUD (`/api/analytics/scheduled-reports`).

Every field the runner reads is validated here, against the runner's OWN
registries rather than a restated copy:

* ``report_type`` against ``services/report_export.EXPORTERS`` — the dict
  ``_generate_report_payload`` looks the exporter up in. A row naming a key that
  isn't there raises ``ValueError`` on every tick and burns through the 5-strike
  auto-disable without ever sending anything.
* ``cadence`` against ``services/scheduled_reports._CADENCE_DELTA`` — the runner
  falls back to daily on an unknown cadence, so an unvalidated value silently
  reschedules a "monthly" report as daily.

Importing the registries means adding a report type or a cadence updates this
surface for free; there is no second list to remember.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.report_export import EXPORTERS
from app.services.scheduled_reports import known_cadences

#: Valid values, derived from the runner's registries (see the module docstring).
REPORT_TYPES: tuple[str, ...] = tuple(sorted(EXPORTERS))
CADENCES: tuple[str, ...] = tuple(sorted(known_cadences()))

#: Same conservative shape check `api/signup.py` and `api/partner.py` use — we
#: don't pull in the `email-validator` dependency for this. Non-dot character
#: classes delimited by literal dots so the engine can't backtrack
#: catastrophically on an adversarial input.
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")

#: A schedule is a recurring outbound email carrying a CSV of the tenant's AP
#: spend, so the recipient list is a distribution surface, not a preference.
#: Bounded on both ends: at least one address (a schedule with none can only
#: fail — the runner marks `failure` on every tick), and few enough that one row
#: can't be turned into a mailing blast.
MAX_RECIPIENTS = 20
MAX_PERIOD_DAYS = 366


def _clean_recipients(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("recipients must be a list of email addresses")
    # Cap the RAW list before doing any per-item work, so an oversized payload
    # is refused rather than parsed. (De-duping first would also let 5000
    # copies of one address slip under the cap.)
    if len(value) > MAX_RECIPIENTS:
        raise ValueError(f"at most {MAX_RECIPIENTS} recipients are allowed")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("recipients must be a list of email addresses")
        address = raw.strip()
        if not _EMAIL_PATTERN.match(address):
            # Never echo the offending value — a recipient list is third-party
            # PII and this message reaches an HTTP error body.
            raise ValueError("recipients must all be valid email addresses")
        key = address.lower()
        if key in seen:
            continue  # de-dupe rather than 422 — a duplicate would double-send
        seen.add(key)
        cleaned.append(address)
    if not cleaned:
        raise ValueError("at least one recipient is required")
    return cleaned


class ScheduledReportCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    report_type: str
    cadence: str
    recipients: list[str]
    period_days: int = Field(default=30, ge=1, le=MAX_PERIOD_DAYS)
    enabled: bool = True
    #: The first slot. Omitted → now, so the schedule runs on the next tick and
    #: the operator can see it work. `advance_next_run` then HOLDS whatever
    #: time-of-day this lands on, so pass an explicit value to pin e.g. 09:00.
    #: A value in the past is legitimate (it is immediately due, then catches up
    #: in whole cadence steps) — deliberately not rejected.
    next_run_at: datetime | None = None

    @field_validator("report_type")
    @classmethod
    def _known_report_type(cls, v: str) -> str:
        if v not in EXPORTERS:
            raise ValueError(f"report_type must be one of: {', '.join(REPORT_TYPES)}")
        return v

    @field_validator("cadence")
    @classmethod
    def _known_cadence(cls, v: str) -> str:
        if v not in CADENCES:
            raise ValueError(f"cadence must be one of: {', '.join(CADENCES)}")
        return v

    @field_validator("recipients")
    @classmethod
    def _valid_recipients(cls, v: Any) -> list[str]:
        return _clean_recipients(v)

    @field_validator("next_run_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        # The column is `DateTime(timezone=True)`; a naive value means UTC, not
        # the server's local zone (see app/utils/dates.py).
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class ScheduledReportUpdate(BaseModel):
    """Partial update — only the supplied fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    report_type: str | None = None
    cadence: str | None = None
    recipients: list[str] | None = None
    period_days: int | None = Field(default=None, ge=1, le=MAX_PERIOD_DAYS)
    enabled: bool | None = None
    next_run_at: datetime | None = None

    @field_validator("report_type")
    @classmethod
    def _known_report_type(cls, v: str | None) -> str | None:
        if v is not None and v not in EXPORTERS:
            raise ValueError(f"report_type must be one of: {', '.join(REPORT_TYPES)}")
        return v

    @field_validator("cadence")
    @classmethod
    def _known_cadence(cls, v: str | None) -> str | None:
        if v is not None and v not in CADENCES:
            raise ValueError(f"cadence must be one of: {', '.join(CADENCES)}")
        return v

    @field_validator("recipients")
    @classmethod
    def _valid_recipients(cls, v: Any) -> list[str] | None:
        if v is None:
            return None
        return _clean_recipients(v)

    @field_validator("next_run_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class ScheduledReportResponse(BaseModel):
    id: uuid.UUID
    name: str
    report_type: str
    cadence: str
    recipients: list[str]
    period_days: int
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_run_error: str | None = None

    @classmethod
    def from_db(cls, row) -> ScheduledReportResponse:
        return cls(
            id=row.id,
            name=row.name,
            report_type=row.report_type,
            cadence=row.cadence,
            recipients=list(row.recipients or []),
            period_days=row.period_days,
            enabled=row.enabled,
            next_run_at=row.next_run_at,
            last_run_at=row.last_run_at,
            last_run_status=row.last_run_status,
            last_run_error=row.last_run_error,
        )


class ScheduledReportListResponse(BaseModel):
    schedules: list[ScheduledReportResponse]
    #: The catalogs the client picks from, so a UI never hardcodes them.
    report_types: list[str] = Field(default_factory=lambda: list(REPORT_TYPES))
    cadences: list[str] = Field(default_factory=lambda: list(CADENCES))
