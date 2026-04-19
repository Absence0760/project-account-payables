"""Quarterly access-review export — every user × role × organization.

Output is a CSV the security officer hands to the auditor (or attaches to
the compliance vendor's evidence locker). Each row is one user-role pair;
users without roles get one row with an empty role column so the report is
still complete.

Run from `backend/`:

    python scripts/access_review.py                       # writes to stdout
    python scripts/access_review.py --out review.csv      # writes to file
    python scripts/access_review.py --include-inactive    # includes deactivated users

This is part of the SOC 2 prerequisite checklist
(`docs/soc2-readiness.md` § Identity, access, and authentication).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import control_engine, control_session_factory
from app.models.organization import Organization
from app.models.user import User


async def collect_rows(*, include_inactive: bool) -> list[dict]:
    """One row per user-role pair. Joins org for the report."""
    async with control_session_factory() as session:
        query = select(User).options(
            selectinload(User.roles),
            selectinload(User.organization),
        )
        if not include_inactive:
            query = query.where(User.is_active.is_(True))
        query = query.order_by(User.email)

        result = await session.execute(query)
        users = result.scalars().all()

    rows: list[dict] = []
    for user in users:
        org: Organization | None = user.organization
        org_name = org.name if org else ""
        org_slug = org.slug if org else ""
        sso = "yes" if user.sso_provider else "no"
        mfa = "yes" if user.mfa_enabled else "no"
        common = {
            "user_id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "is_active": "yes" if user.is_active else "no",
            "must_change_password": "yes" if user.must_change_password else "no",
            "mfa_enabled": mfa,
            "sso_provider": user.sso_provider or "",
            "organization_id": str(user.organization_id),
            "organization_name": org_name,
            "organization_slug": org_slug,
            "uses_sso": sso,
            "created_at": user.created_at.isoformat() if user.created_at else "",
        }
        if not user.roles:
            rows.append({**common, "role": ""})
            continue
        for role in user.roles:
            rows.append({**common, "role": role.name})

    return rows


def write_csv(rows: list[dict], out) -> None:
    fieldnames = [
        "user_id",
        "email",
        "full_name",
        "role",
        "organization_id",
        "organization_name",
        "organization_slug",
        "is_active",
        "must_change_password",
        "mfa_enabled",
        "uses_sso",
        "sso_provider",
        "created_at",
    ]
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)


async def main(args: argparse.Namespace) -> None:
    rows = await collect_rows(include_inactive=args.include_inactive)
    if args.out:
        with open(args.out, "w", newline="") as f:
            write_csv(rows, f)
        print(
            f"Wrote {len(rows)} row(s) to {args.out} "
            f"(generated at {datetime.now(UTC).isoformat()})",
            file=sys.stderr,
        )
    else:
        write_csv(rows, sys.stdout)
    await control_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="CSV output path; default stdout")
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include deactivated users (default: active only)",
    )
    asyncio.run(main(parser.parse_args()))
