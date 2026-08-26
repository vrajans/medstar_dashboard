"""
api/routers/adhoc.py
Persisted ad-hoc datasets — the "Explore" playground that flows through the real
pipeline: ingest (store rows) → profile (ETL) → warehouse (Neon, tenant-scoped,
durable) → serve. Data survives across sessions and is governed by tenant RBAC.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select, delete, func as safunc

from ..deps import CurrentUser, DBSession
from ..models import AdhocDataset

# make the app-root modules (ai.llm_gateway) importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

router = APIRouter(prefix="/adhoc", tags=["Ad hoc"])

MAX_ROWS = 50_000


class DatasetCreate(BaseModel):
    name: str
    tenant_id: Optional[int] = None
    rows: list[dict]


def _can_access(user, ds_tenant_id) -> bool:
    """RBAC: a tenant user may only touch their own datasets; admins (no tenant) all."""
    utid = getattr(user, "tenant_id", None)
    if utid is None:            # internal/admin/operator → all customers
        return True
    return int(ds_tenant_id) == int(utid) if ds_tenant_id is not None else False


@router.post("/datasets", summary="Save an ad-hoc dataset (ingest → warehouse)")
async def create_dataset(body: DatasetCreate, user: CurrentUser, db: DBSession):
    rows = (body.rows or [])[:MAX_ROWS]
    if not rows:
        raise HTTPException(400, "No rows to save.")
    cols = list(rows[0].keys())
    # tenant scope: a tenant user's data is forced to their tenant
    utid = getattr(user, "tenant_id", None)
    tid = utid if utid is not None else body.tenant_id
    ds = AdhocDataset(tenant_id=tid, name=body.name.strip() or "Untitled dataset",
                      columns=cols, row_count=len(rows), data=rows)
    db.add(ds)
    await db.flush()
    await db.refresh(ds)
    return {"id": ds.id, "name": ds.name, "row_count": ds.row_count, "columns": cols}


@router.get("/datasets", summary="List saved datasets for a tenant")
async def list_datasets(user: CurrentUser, db: DBSession, tenant_id: Optional[int] = Query(None)):
    utid = getattr(user, "tenant_id", None)
    scope = utid if utid is not None else tenant_id
    q = select(AdhocDataset.id, AdhocDataset.name, AdhocDataset.row_count,
               AdhocDataset.created_at).order_by(AdhocDataset.id.desc())
    if scope is not None:
        q = q.where(AdhocDataset.tenant_id == scope)
    res = await db.execute(q)
    return [{"id": r.id, "name": r.name, "row_count": r.row_count,
             "created_at": str(r.created_at)[:19]} for r in res.all()]


async def _load(db, ds_id, user) -> AdhocDataset:
    res = await db.execute(select(AdhocDataset).where(AdhocDataset.id == ds_id))
    ds = res.scalar_one_or_none()
    if not ds:
        raise HTTPException(404, "Dataset not found")
    if not _can_access(user, ds.tenant_id):
        raise HTTPException(403, "Not permitted for this dataset")
    return ds


@router.get("/datasets/{ds_id}", summary="Open a dataset (rows for analysis)")
async def get_dataset(ds_id: int, user: CurrentUser, db: DBSession):
    ds = await _load(db, ds_id, user)
    return {"id": ds.id, "name": ds.name, "columns": ds.columns,
            "row_count": ds.row_count, "rows": ds.data}


@router.delete("/datasets/{ds_id}", summary="Delete a dataset")
async def delete_dataset(ds_id: int, user: CurrentUser, db: DBSession):
    ds = await _load(db, ds_id, user)
    await db.execute(delete(AdhocDataset).where(AdhocDataset.id == ds.id))
    return {"ok": True}


def _profile_summary(columns: list, rows: list) -> str:
    """Compact, LLM-friendly profile of the dataset."""
    n = len(rows)
    lines = [f"Dataset: {n} rows, {len(columns)} columns."]
    for c in columns[:25]:
        vals = [r.get(c) for r in rows if r.get(c) not in (None, "")]
        if not vals:
            lines.append(f"- {c}: all blank"); continue
        nums = []
        for v in vals[:2000]:
            try:
                nums.append(float(str(v).replace(",", "").replace("$", "").replace("%", "")))
            except (ValueError, TypeError):
                pass
        if len(nums) >= 0.8 * len(vals[:2000]) and nums:
            lines.append(f"- {c} (number): min {min(nums):.2f}, max {max(nums):.2f}, "
                         f"avg {sum(nums)/len(nums):.2f}")
        else:
            uniq = {}
            for v in vals:
                uniq[str(v)] = uniq.get(str(v), 0) + 1
            top = sorted(uniq.items(), key=lambda x: -x[1])[:5]
            lines.append(f"- {c} (category, {len(uniq)} unique): "
                         + ", ".join(f"{k}={v}" for k, v in top))
    return "\n".join(lines)


@router.post("/datasets/{ds_id}/insights", summary="AI narrative for a dataset")
async def dataset_insights(ds_id: int, user: CurrentUser, db: DBSession):
    ds = await _load(db, ds_id, user)
    summary = _profile_summary(ds.columns or [], ds.data or [])
    try:
        from ai import llm_gateway as g
    except Exception:
        return {"insight": "AI module unavailable on the server."}
    cfg = g.resolve_config(tenant_id=ds.tenant_id)
    if not cfg.api_key:
        return {"insight": "No AI provider key configured on the backend."}
    messages = [
        {"role": "system", "content":
            "You are a data analyst. Given a dataset profile, state the 3–5 most "
            "important observations and any anomalies in plain language. Be concise, "
            "no preamble, no bullet headers longer than a sentence."},
        {"role": "user", "content": summary},
    ]
    ans = await run_in_threadpool(g.chat, messages, config=cfg, max_tokens=350, temperature=0.4)
    return {"insight": ans or "Could not generate an insight right now."}
