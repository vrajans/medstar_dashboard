"""
api/routers/analytics.py
Customer-facing analytics endpoints for the Next.js app.
Reads from the dimensional warehouse serving mart (wh_mart_transaction),
scoped by tenant. JWT-protected via CurrentUser.

NOTE: tenant scoping — in this scaffold tenant_id is an explicit query param.
In production, derive it from the authenticated user's tenant claim and reject
requests for any other tenant (the SQL is already tenant-parameterized).
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Query
from sqlalchemy import text

from ..deps import CurrentUser, DBSession

router = APIRouter(prefix="/analytics", tags=["Analytics"])


def _tenant_clause(tenant_id: Optional[int]):
    if tenant_id is None:
        return "tenant_id IS NULL", {}
    return "tenant_id = :t", {"t": tenant_id}


@router.get("/overview", summary="Headline KPIs for a tenant")
async def overview(user: CurrentUser, db: DBSession, tenant_id: Optional[int] = Query(None)):
    where, params = _tenant_clause(tenant_id)
    q = await db.execute(text(f"""
        SELECT
            COALESCE(SUM(CASE WHEN txn_type='sale'     THEN net_amount END), 0) AS revenue,
            COALESCE(SUM(CASE WHEN txn_type='purchase' THEN net_amount END), 0) AS costs,
            COALESCE(AVG(CASE WHEN txn_type='sale' AND margin_pct <> 0 THEN margin_pct END), 0) AS avg_margin,
            COALESCE(SUM(CASE WHEN txn_type='sale'     THEN txn_count END), 0) AS txns,
            COUNT(DISTINCT CASE WHEN txn_type='sale' THEN party_name END) AS customers
        FROM wh_mart_transaction WHERE {where}
    """), params)
    row = q.mappings().first() or {}
    revenue = float(row.get("revenue") or 0)
    costs   = float(row.get("costs") or 0)
    return {
        "tenant_id": tenant_id,
        "revenue":   revenue,
        "costs":     costs,
        "gross_margin_pct": round(((revenue - costs) / revenue * 100) if revenue else 0, 1),
        "avg_margin_pct":   round(float(row.get("avg_margin") or 0), 1),
        "transactions":     int(row.get("txns") or 0),
        "customers":        int(row.get("customers") or 0),
        "net_cash_flow":    revenue - costs,
    }


@router.get("/timeseries", summary="Monthly revenue vs cost")
async def timeseries(user: CurrentUser, db: DBSession, tenant_id: Optional[int] = Query(None)):
    where, params = _tenant_clause(tenant_id)
    q = await db.execute(text(f"""
        SELECT year, month, month_name,
               COALESCE(SUM(CASE WHEN txn_type='sale'     THEN net_amount END), 0) AS revenue,
               COALESCE(SUM(CASE WHEN txn_type='purchase' THEN net_amount END), 0) AS cost,
               COALESCE(AVG(CASE WHEN txn_type='sale' AND margin_pct <> 0 THEN margin_pct END), 0) AS margin
        FROM wh_mart_transaction WHERE {where}
        GROUP BY year, month, month_name
        ORDER BY year, month
    """), params)
    series = []
    cumulative = 0.0
    for r in q.mappings().all():
        rev = float(r["revenue"] or 0)
        cost = float(r["cost"] or 0)
        cumulative += rev
        series.append({
            "period": f"{r['month_name']} {r['year']}",
            "year": int(r["year"]), "month": int(r["month"]),
            "revenue": rev, "cost": cost, "net": rev - cost,
            "cumulative": cumulative,
            "margin": round(float(r["margin"] or 0), 1),
        })
    return {"tenant_id": tenant_id, "series": series}


@router.get("/suppliers", summary="Top suppliers by purchase spend")
async def suppliers(user: CurrentUser, db: DBSession,
                    tenant_id: Optional[int] = Query(None), limit: int = Query(10, le=50)):
    where, params = _tenant_clause(tenant_id)
    params = {**params, "lim": limit}
    q = await db.execute(text(f"""
        SELECT COALESCE(party_name, entity_name) AS name,
               COALESCE(SUM(net_amount), 0) AS total,
               COALESCE(SUM(txn_count), 0)  AS txns
        FROM wh_mart_transaction
        WHERE {where} AND txn_type='purchase' AND COALESCE(party_name, entity_name) IS NOT NULL
        GROUP BY COALESCE(party_name, entity_name)
        ORDER BY total DESC
        LIMIT :lim
    """), params)
    return {"tenant_id": tenant_id,
            "suppliers": [{"name": r["name"], "total": float(r["total"] or 0),
                           "transactions": int(r["txns"] or 0)} for r in q.mappings().all()]}


@router.get("/top-entities", summary="Top clients / branches by revenue")
async def top_entities(user: CurrentUser, db: DBSession,
                       tenant_id: Optional[int] = Query(None), limit: int = Query(10, le=50)):
    where, params = _tenant_clause(tenant_id)
    params = {**params, "lim": limit}
    q = await db.execute(text(f"""
        SELECT COALESCE(party_name, entity_name) AS name,
               COALESCE(SUM(net_amount), 0) AS total,
               COALESCE(SUM(txn_count), 0)  AS txns
        FROM wh_mart_transaction
        WHERE {where} AND txn_type='sale' AND COALESCE(party_name, entity_name) IS NOT NULL
        GROUP BY COALESCE(party_name, entity_name)
        ORDER BY total DESC
        LIMIT :lim
    """), params)
    return {"tenant_id": tenant_id,
            "entities": [{"name": r["name"], "total": float(r["total"] or 0),
                          "transactions": int(r["txns"] or 0)} for r in q.mappings().all()]}
