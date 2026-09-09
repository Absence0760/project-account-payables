"""One-shot CLI: sweep stale approval-chain levels and apply escalations.

Same logic the background sweeper runs on a timer — surfaced as a
script for ad-hoc one-off runs (CI smoke, post-deploy verification,
or environments where the sweeper loop hasn't been deployed yet).

Usage (from `backend/`):

    python scripts/sweep_approval_escalations.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Anchor THIS checkout's `backend/` on sys.path before `app` is imported below.
# Run as a script, sys.path[0] is `scripts/`, so `app` is not on sys.path at
# all — and a worktree reusing the primary checkout's `backend/.venv` resolves
# it through the editable install's baked-in `__editable___backend_0_1_0_finder`,
# acting on the WRONG tree with nothing on screen to say so. Inserted at 1 so
# the script's own directory keeps priority; idempotent, and a no-op in the
# checkout the venv was installed from.
#
# Deliberately NOT hoisted into a variable: ruff's E402 exempts `sys.path`
# manipulation before imports but not an assignment beside it, so naming the
# path here turns every import below into a lint error.
# See `frontend/tests-e2e/README.md` § Running from a worktree.
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(1, str(Path(__file__).resolve().parent.parent))

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
