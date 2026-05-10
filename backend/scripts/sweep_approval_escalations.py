"""One-shot CLI: sweep stale approval-chain levels and apply escalations.

Same logic the background sweeper runs on a timer — surfaced as a
script for ad-hoc one-off runs (CI smoke, post-deploy verification,
or environments where the sweeper loop hasn't been deployed yet).

Usage (from `backend/`):

    python scripts/sweep_approval_escalations.py
"""

from __future__ import annotations

import asyncio

from app.services.approval_escalation import escalate_once


async def main() -> None:
    result = await escalate_once()
    print(
        f"Swept {result.tenants_scanned} tenant(s); "
        f"escalated {result.instances_escalated} instance(s); "
        f"{result.failures} sweep failure(s)."
    )


if __name__ == "__main__":
    asyncio.run(main())
