"""
api/routers/pi.py — Payment Integrity analysis endpoint.
Runs the claims through the PI engine (repo-root payment_integrity.py) and
returns a prioritized worklist + provider scorecard + summary, with an optional
AI executive narrative.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..deps import CurrentUser, DBSession
from ..models import AdhocDataset, PIRun
from sqlalchemy import select, delete

# make the app-root modules importable (payment_integrity, ai.llm_gateway)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

router = APIRouter(prefix="/pi", tags=["Payment Integrity"])


class AnalyzeBody(BaseModel):
    rows: Optional[list[dict]] = None
    dataset_id: Optional[int] = None


@router.post("/analyze", summary="Run payment-integrity checks on claims")
async def analyze(body: AnalyzeBody, user: CurrentUser, db: DBSession):
    rows = body.rows
    if not rows and body.dataset_id is not None:
        res = await db.execute(select(AdhocDataset).where(AdhocDataset.id == body.dataset_id))
        ds = res.scalar_one_or_none()
        if not ds:
            raise HTTPException(404, "Dataset not found")
        rows = ds.data
    if not rows:
        raise HTTPException(400, "No claims provided.")

    import pandas as pd
    import payment_integrity as pi
    df = pd.DataFrame(rows)
    result = await run_in_threadpool(pi.analyze_claims, df)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class RunCreate(BaseModel):
    name: str
    tenant_id: Optional[int] = None
    rows: list[dict]


def _pi_can_access(user, tid) -> bool:
    utid = getattr(user, "tenant_id", None)
    if utid is None:
        return True
    return tid is not None and int(tid) == int(utid)


@router.post("/runs", summary="Ingest claims → warehouse, analyze, and SAVE the run")
async def create_run(body: RunCreate, user: CurrentUser, db: DBSession):
    rows = (body.rows or [])[:50_000]
    if not rows:
        raise HTTPException(400, "No claims provided.")
    utid = getattr(user, "tenant_id", None)
    tid = utid if utid is not None else body.tenant_id

    # 1) INGEST — claims land in the warehouse as a dataset (lineage)
    cols = list(rows[0].keys())
    ds = AdhocDataset(tenant_id=tid, name=f"[claims] {body.name}", columns=cols,
                      row_count=len(rows), data=rows)
    db.add(ds)
    await db.flush()
    await db.refresh(ds)

    # 2) ETL — run the payment-integrity engine
    import pandas as pd
    import payment_integrity as pi
    result = await run_in_threadpool(pi.analyze_claims, pd.DataFrame(rows))
    if "error" in result:
        raise HTTPException(400, result["error"])
    s = result["summary"]

    # 3) WAREHOUSE — persist the run + results
    run = PIRun(tenant_id=tid, name=body.name.strip() or "PI run", dataset_id=ds.id,
                row_count=s["total_claims"], total_paid=s["total_paid"],
                amount_at_risk=s["amount_at_risk"], pct_at_risk=s["pct_at_risk"],
                flagged_claims=s["flagged_claims"], result=result)
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return {"id": run.id, "name": run.name, "result": result}


@router.get("/runs", summary="List saved PI runs for a tenant")
async def list_runs(user: CurrentUser, db: DBSession, tenant_id: Optional[int] = None):
    utid = getattr(user, "tenant_id", None)
    scope = utid if utid is not None else tenant_id
    q = select(PIRun.id, PIRun.name, PIRun.row_count, PIRun.total_paid,
               PIRun.amount_at_risk, PIRun.pct_at_risk, PIRun.flagged_claims,
               PIRun.created_at).order_by(PIRun.id.desc())
    if scope is not None:
        q = q.where(PIRun.tenant_id == scope)
    res = await db.execute(q)
    return [{"id": r.id, "name": r.name, "row_count": r.row_count,
             "total_paid": r.total_paid, "amount_at_risk": r.amount_at_risk,
             "pct_at_risk": r.pct_at_risk, "flagged_claims": r.flagged_claims,
             "created_at": str(r.created_at)[:19]} for r in res.all()]


@router.get("/runs/{run_id}", summary="Open a saved PI run")
async def get_run(run_id: int, user: CurrentUser, db: DBSession):
    res = await db.execute(select(PIRun).where(PIRun.id == run_id))
    run = res.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    if not _pi_can_access(user, run.tenant_id):
        raise HTTPException(403, "Not permitted")
    return {"id": run.id, "name": run.name, "result": run.result}


@router.delete("/runs/{run_id}", summary="Delete a saved PI run")
async def delete_run(run_id: int, user: CurrentUser, db: DBSession):
    res = await db.execute(select(PIRun).where(PIRun.id == run_id))
    run = res.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    if not _pi_can_access(user, run.tenant_id):
        raise HTTPException(403, "Not permitted")
    if run.dataset_id:
        await db.execute(delete(AdhocDataset).where(AdhocDataset.id == run.dataset_id))
    await db.execute(delete(PIRun).where(PIRun.id == run.id))
    return {"ok": True}


class NarrativeBody(BaseModel):
    summary: dict
    top_findings: list[dict] = []


@router.post("/narrative", summary="AI executive summary of the PI results")
async def narrative(body: NarrativeBody, user: CurrentUser, db: DBSession):
    try:
        from ai import llm_gateway as g
    except Exception:
        return {"narrative": "AI module unavailable."}
    cfg = g.resolve_config()
    if not cfg.api_key:
        return {"narrative": "No AI provider key configured on the backend."}

    s = body.summary or {}
    cats = "; ".join(f"{c['category']}: {c['count']} claims, ${c['amount']:,.0f}"
                     for c in s.get("by_category", [])[:6])
    tops = "; ".join(f"{f.get('category')} — {f.get('reason')}" for f in (body.top_findings or [])[:5])
    prompt = (
        f"Payment integrity scan of {s.get('total_claims',0)} claims (${s.get('total_paid',0):,.0f} paid). "
        f"Flagged {s.get('flagged_claims',0)} claims, ${s.get('amount_at_risk',0):,.0f} at risk "
        f"({s.get('pct_at_risk',0)}%). Categories: {cats}. Examples: {tops}.\n"
        "Write a 4-5 sentence executive summary for a payment-integrity manager: the biggest "
        "exposure, which categories to prioritize for recovery, and the recommended next action. "
        "Be specific and concise; no preamble."
    )
    messages = [
        {"role": "system", "content": "You are a healthcare payment-integrity analyst."},
        {"role": "user", "content": prompt},
    ]
    ans = await run_in_threadpool(g.chat, messages, config=cfg, max_tokens=350, temperature=0.4)
    return {"narrative": ans or "Could not generate a narrative right now."}
