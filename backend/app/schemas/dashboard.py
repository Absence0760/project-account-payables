from pydantic import BaseModel


class StatusCount(BaseModel):
    status: str
    count: int


class DashboardResponse(BaseModel):
    total_invoices: int
    total_amount: float
    status_counts: list[StatusCount]
