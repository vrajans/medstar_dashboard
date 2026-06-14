"""
api/routers/purchases.py
Purchase / GRN data endpoints — all protected by JWT.

GET  /purchases          paginated list with branch/date/supplier filters
GET  /purchases/summary  aggregated KPIs
GET  /purchases/branches distinct branch list
GET  /purchases/suppliers top-N supplier totals
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Query
from sqlalchemy import distinct, func, select

from ..deps import CurrentUser, DBSession
from ..models import PurchaseRecord
from ..schemas import PaginatedResponse, PurchaseOut, PurchaseSummary

router = APIRouter(prefix="/purchases", tags=["Purchases"])


def _base_query(
    branch:   Optional[str],
    start:    Optional[str],
    end:      Optional[str],
    supplier: Optional[str],
):
    q = select(PurchaseRecord)
    if branch:
        q = q.where(PurchaseRecord.branch == branch)
    if start:
        q = q.where(PurchaseRecord.grn_date >= start)
    if end:
        q = q.where(PurchaseRecord.grn_date <= end)
    if supplier:
        q = q.where(PurchaseRecord.supplier_name.ilike(f"%{supplier}%"))
    return q


@router.get("", response_model=PaginatedResponse, summary="List purchase records (paginated)")
async def list_purchases(
    db:       DBSession,
    _user:    CurrentUser,
    branch:   Annotated[Optional[str], Query()] = None,
    start:    Annotated[Optional[str], Query(description="GRN date start YYYY-MM-DD")] = None,
    end:      Annotated[Optional[str], Query(description="GRN date end YYYY-MM-DD")]   = None,
    supplier: Annotated[Optional[str], Query(description="Partial supplier name match")] = None,
    page:     Annotated[int, Query(ge=1)] = 1,
    size:     Annotated[int, Query(ge=1, le=500)] = 50,
):
    base = _base_query(branch, start, end, supplier)

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    offset = (page - 1) * size
    result = await db.execute(
        base.order_by(PurchaseRecord.grn_date.desc(), PurchaseRecord.branch)
            .offset(offset)
            .limit(size)
    )
    rows = result.scalars().all()

    return PaginatedResponse(
        items = [PurchaseOut.model_validate(r) for r in rows],
        total = total,
        page  = page,
        size  = size,
        pages = max(1, -(-total // size)),
    )


@router.get("/summary", response_model=PurchaseSummary, summary="Aggregated purchase KPIs")
async def purchase_summary(
    db:       DBSession,
    _user:    CurrentUser,
    branch:   Annotated[Optional[str], Query()] = None,
    start:    Annotated[Optional[str], Query()] = None,
    end:      Annotated[Optional[str], Query()] = None,
    supplier: Annotated[Optional[str], Query()] = None,
):
    base = _base_query(branch, start, end, supplier).subquery()

    agg = await db.execute(
        select(
            func.sum(base.c.net_amount).label("net"),
            func.sum(base.c.gross_amount).label("gross"),
            func.sum(base.c.total_gst).label("gst"),
            func.sum(base.c.discount_pct).label("discount"),
            func.count(distinct(base.c.supplier_name)).label("suppliers"),
            func.count(distinct(base.c.grn_number)).label("grns"),
            func.min(base.c.grn_date).label("period_start"),
            func.max(base.c.grn_date).label("period_end"),
            func.count().label("row_count"),
        )
    )
    row = agg.one()

    return PurchaseSummary(
        branch          = branch,
        total_net       = float(row.net      or 0),
        total_gross     = float(row.gross    or 0),
        total_gst       = float(row.gst      or 0),
        total_discount  = float(row.discount or 0),
        supplier_count  = row.suppliers or 0,
        grn_count       = row.grns      or 0,
        period_start    = row.period_start,
        period_end      = row.period_end,
        row_count       = row.row_count or 0,
    )


@router.get("/branches", summary="Distinct branches in purchase data")
async def purchase_branches(db: DBSession, _user: CurrentUser):
    result = await db.execute(
        select(distinct(PurchaseRecord.branch)).order_by(PurchaseRecord.branch)
    )
    return {"branches": result.scalars().all()}


@router.get("/suppliers", summary="Top-N suppliers by net purchase amount")
async def top_suppliers(
    db:     DBSession,
    _user:  CurrentUser,
    branch: Annotated[Optional[str], Query()] = None,
    start:  Annotated[Optional[str], Query()] = None,
    end:    Annotated[Optional[str], Query()] = None,
    limit:  Annotated[int, Query(ge=1, le=100)] = 15,
):
    base = _base_query(branch, start, end, None).subquery()
    result = await db.execute(
        select(
            base.c.supplier_name,
            func.sum(base.c.net_amount).label("total_net"),
            func.count(distinct(base.c.grn_number)).label("grn_count"),
        )
        .group_by(base.c.supplier_name)
        .order_by(func.sum(base.c.net_amount).desc())
        .limit(limit)
    )
    return {
        "suppliers": [
            {"supplier_name": r.supplier_name, "total_net": float(r.total_net or 0), "grn_count": r.grn_count}
            for r in result.all()
        ]
    }
