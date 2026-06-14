"""Procurement / Requisitions — intake router.

Foundation stub: the data model (app/models/procurement.py) + migration
0041_procurement are shipped; this router is implemented by the intake vertical.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/intake", tags=["intake"])
