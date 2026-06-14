"""
api/routers/sales.py
Sales data endpoints — all protected by JWT (any authenticated user).

GET  /sales              paginated list with branch/date filters
GET  /sales/summary      aggregated KPIs for a branch/period
GET  /sales/branches     distinct branch list
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Query
from sqlalchemy import distinct, func, select

from ..deps import CurrentUser, DBSession
from ..models import SalesRecord
from ..schemas import PaginatedResponse, SalesOut, SalesSummary

router = APIRouter(prefix="/sales", tags=["Sales"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _base_query(branch: Optional[str], start: Optional[str], end: Optional[str]):
    q = select(SalesRecord)
    if branch:
        q = q.where(SalesRecord.branch == branch)
    if start:
        q = q.where(SalesRecord.bill_date >= start)
    if end:
        q = q.where(SalesRecord.bill_date <= end)
    return q


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse, summary="List sales records (paginated)")
async def list_sales(
    db:     DBSession,
    _user:  CurrentUser,
    branch: Annotated[Optional[str], Query(description="Filter by branch name")] = None,
    start:  Annotated[Optional[str], Query(description="Start date YYYY-MM-DD")] = None,
    end:    Annotated[Optional[str], Query(description="End date YYYY-MM-DD")]   = None,
    page:   Annotated[int, Query(ge=1)]  = 1,
    size:   Annotated[int, Query(ge=1, le=500)] = 50,
):
    base = _base_query(branch, start, end)

    # Count
    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    # Page
    offset = (page - 1) * size
    result = await db.execute(
        base.order_by(SalesRecord.bill_date.desc(), SalesRecord.branch)
            .offset(offset)
            .limit(size)
    )
    rows = result.scalars().all()

    return PaginatedResponse(
        items = [SalesOut.model_validate(r) for r in rows],
        total = total,
        page  = page,
        size  = size,
        pages = max(1, -(-total // size)),  # ceiling division
    )


@router.get("/summary", response_model=SalesSummary, summary="Aggregated sales KPIs")
async def sales_summary(
    db:     DBSession,
    _user:  CurrentUser,
    branch: Annotated[Optional[str], Query()] = None,
    start:  Annotated[Optional[str], Query()] = None,
    end:    Annotated[Optional[str], Query()] = None,
):
    base = _base_query(branch, start, end).subquery()

    agg = await db.execute(
        select(
            func.sum(base.c.net_amount).label("revenue"),
            func.sum(base.c.cost_of_sales).label("cost"),
            func.sum(base.c.total_bills).label("bills"),
            func.sum(base.c.return_count).label("returns"),
            func.sum(base.c.pharma_sales).label("pharma"),
            func.min(base.c.bill_date).label("period_start"),
            func.max(base.c.bill_date).label("period_end"),
            func.count().label("row_count"),
        )
    )
    row = agg.one()

    revenue = float(row.revenue or 0)
    cost    = float(row.cost    or 0)
    pharma  = float(row.pharma  or 0)
    margin  = ((revenue - cost) / revenue * 100) if revenue else 0.0
    pharma_pct = (pharma / revenue * 100) if revenue else 0.0

    return SalesSummary(
        branch       = branch,
        total_revenue= revenue,
        total_cost   = cost,
        gross_margin = round(margin, 2),
        total_bills  = float(row.bills   or 0),
        return_count = float(row.returns or 0),
        pharma_pct   = round(pharma_pct, 2),
        period_start = row.period_start,
        period_end   = row.period_end,
        row_count    = row.row_count or 0,
    )


@router.get("/branches", summary="List distinct branches in sales data")
async def sales_branches(db: DBSession, _user: CurrentUser):
    result = await db.execute(
        select(distinct(SalesRecord.branch)).order_by(SalesRecord.branch)
    )
    return {"branches": result.scalars().all()}
