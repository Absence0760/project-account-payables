"""Procurement / Requisitions — budgets router.

Foundation stub: the data model (app/models/procurement.py) + migration
0041_procurement are shipped; this router is implemented by the budgets vertical.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/budgets", tags=["budgets"])
