"""One definition of "today" for the backend.

``date.today()`` resolves in the **server's local timezone**. On the deployed
shape (a UTC container) that agrees with UTC, so a mixture is latent rather than
live — which is exactly what makes it easy to keep introducing.

It stops being latent the moment anything runs off a non-UTC host, because the
cash-flow stack derives more than a display value from "today":

* ``api/analytics._commitment_rows`` bounds its horizon on it, so the `/cfo`
  chart and a copilot answer built minutes apart can be assembled from
  *different row sets* for part of each day;
* ``services/cash_flow_plan.compute_plan_id`` hashes it, so a plan proposed near
  midnight can 409 as stale against its own enact call;
* ``services/scheduled_reports`` slices each report's ``period_days`` window
  from it, so an emailed snapshot and the API export of the same report
  disagree at the boundary.

…and the AP surfaces do too. Every one of these is a *comparison*, not a label:

* the early-pay discount cutoff — ``api/discounts``, ``api/portal`` (the
  supplier's own view of the same offer) and ``services/analytics`` all ask
  "is this tier still capturable", and a tenant could see one answer while its
  supplier saw the other;
* ``services/invoice_warnings`` raises a fraud flag when an invoice is dated in
  the future and a past-due flag when its due date has passed — the boundary
  day is where a legitimate invoice becomes a fraud signal;
* ``api/recurring`` derives the period key from it, and that key IS the
  generation idempotency guard (``uq_invoice_recurring_period``), so a shifted
  "today" is a duplicate payable rather than a cosmetic difference;
* ``Invoice.approval_date`` is written from it on all three approval paths
  (``services/review``, ``services/extraction``, ``api/workflow``) — a
  regulated field on the SOX trail.

The stamps and filenames matter less individually, but each sits beside a
``datetime.now(UTC)`` in the same response — a 1099 form's ``generated_at``, a
Positive Pay file's ``file_date`` next to a run's tz-aware ``executed_at``, an
export filename next to the PDF's own provenance header. A local-time answer
made one document disagree with itself for part of each day.

``app/api/cash_flow.py`` and every copilot tool already resolved it as
``datetime.now(UTC).date()``. This module is that expression with a name, so the
rest of the backend converges on it instead of each site re-deciding. No call
site under ``app/`` reads the local date any more; ``scripts/seed*.py`` still
does, deliberately — demo fixtures on a dev laptop have nothing to reconcile
against.

Guard: ``tests/test_utc_today.py`` holds the allowlist of converged modules and
AST-scans each, failing on ``date.today()``, ``datetime.today()``,
``datetime.date.today()`` and ``datetime.now().date()`` — that last one is naive
``now()``, so a local date under a name that reads as deliberate, and a scan
shaped only around the word ``today`` would never see it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

__all__ = ["utc_today"]


def utc_today() -> date:
    """Today's date in UTC — never the server's local timezone."""
    return datetime.now(UTC).date()
