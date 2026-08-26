"""
api/routers/ai.py
Customer-facing AI chat endpoint for the Next.js app.
Grounds the answer in the tenant's serving-mart summary and routes through the
LLM Gateway (platform default, or the tenant's BYO provider when configured).
"""
from __future__ import annotations

import sys, os
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text

from ..deps import CurrentUser, DBSession

# make the app-root modules (ai.llm_gateway) importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

router = APIRouter(prefix="/ai", tags=["AI"])


class ChatRequest(BaseModel):
    message: str
    tenant_id: Optional[int] = None
    history: list[dict] = []


async def _build_context(db, tenant_id: Optional[int]) -> str:
    """Ground the AI in WHATEVER data this tenant actually has: sales/purchases
    marts, the latest Payment-Integrity run, and any saved ad-hoc datasets."""
    where = "tenant_id = :t" if tenant_id is not None else "tenant_id IS NULL"
    params = {"t": tenant_id} if tenant_id is not None else {}
    parts: list[str] = []

    # 1) sales / purchases marts (only if there's meaningful data)
    try:
        q = await db.execute(text(f"""
            SELECT COALESCE(SUM(CASE WHEN txn_type='sale'     THEN net_amount END),0) rev,
                   COALESCE(SUM(CASE WHEN txn_type='purchase' THEN net_amount END),0) cost,
                   COALESCE(AVG(CASE WHEN txn_type='sale' AND margin_pct<>0 THEN margin_pct END),0) margin,
                   COUNT(DISTINCT CASE WHEN txn_type='sale' THEN party_name END) custs
            FROM wh_mart_transaction WHERE {where}
        """), params)
        r = q.mappings().first() or {}
        rev, cost = float(r.get("rev") or 0), float(r.get("cost") or 0)
        if rev or cost:
            parts.append(f"Sales/purchases: revenue {rev:,.0f}, cost {cost:,.0f}, "
                         f"net {rev - cost:,.0f}, avg margin {float(r.get('margin') or 0):.1f}%, "
                         f"{int(r.get('custs') or 0)} customers.")
    except Exception:
        pass

    # 2) latest Payment-Integrity run
    try:
        from ..models import PIRun
        from sqlalchemy import select
        pq = select(PIRun).order_by(PIRun.id.desc())
        if tenant_id is not None:
            pq = pq.where(PIRun.tenant_id == tenant_id)
        run = (await db.execute(pq.limit(1))).scalars().first()
        if run and run.result:
            s = run.result.get("summary", {})
            cats = "; ".join(f"{c['category']}: {c['count']} claims ${c['amount']:,.0f}"
                             for c in s.get("by_category", [])[:6])
            tops = "; ".join(f"{f.get('category')} — {f.get('reason')}"
                             for f in run.result.get("findings", [])[:5])
            provs = "; ".join(f"{p['provider_name']} (risk {p['risk_score']})"
                              for p in run.result.get("providers", [])[:3] if p.get("risk_score", 0) >= 40)
            parts.append(
                f"Payment Integrity run '{run.name}': {run.row_count} claims, "
                f"${run.total_paid:,.0f} paid, {run.flagged_claims} flagged, "
                f"${run.amount_at_risk:,.0f} at risk ({run.pct_at_risk}%). "
                f"Categories: {cats}. Example findings: {tops}. "
                f"High-risk providers: {provs or 'none'}.")
    except Exception:
        pass

    # 3) saved ad-hoc datasets
    try:
        from ..models import AdhocDataset
        from sqlalchemy import select
        aq = select(AdhocDataset.name, AdhocDataset.row_count).order_by(AdhocDataset.id.desc())
        if tenant_id is not None:
            aq = aq.where(AdhocDataset.tenant_id == tenant_id)
        ads = (await db.execute(aq.limit(5))).all()
        if ads:
            parts.append("Saved datasets: " + "; ".join(f"{a.name} ({a.row_count} rows)" for a in ads))
    except Exception:
        pass

    if not parts:
        return f"Tenant {tenant_id} has no data uploaded yet."
    return "Data available for this customer:\n- " + "\n- ".join(parts)


@router.post("/chat", summary="Ask a question about your data")
async def chat(body: ChatRequest, user: CurrentUser, db: DBSession):
    if not (body.message or "").strip():
        return {"answer": "Please type a question.", "grounded": False}

    context = await _build_context(db, body.tenant_id)
    try:
        from ai import llm_gateway as g
    except Exception as e:
        return {"answer": f"AI module unavailable on the server ({e}).", "grounded": False}

    # Resolve the provider once so we can report a precise reason on failure.
    cfg = g.resolve_config(tenant_id=body.tenant_id)
    if not cfg.api_key:
        return {"answer": ("⚠️ No AI provider key is configured on the backend. "
                           "Set GROQ_API_KEY in your .env (or configure a provider in "
                           "AI Settings on the admin app), then restart the API."),
                "grounded": False}

    system = ("You are InsightHub AI, a concise business analytics assistant. "
              "Answer ONLY from the data context provided; if the data is insufficient, say so. "
              "Never invent numbers. Keep answers under 150 words.\n\n" + context)
    messages = [{"role": "system", "content": system}]
    # keep only role/content from prior turns; the current question is appended last
    for m in (body.history or [])[-8:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": body.message})

    try:
        answer = await run_in_threadpool(
            g.chat, messages, max_tokens=400, temperature=0.4, config=cfg)
    except Exception as e:
        return {"answer": f"⚠️ AI request errored: {e}", "grounded": False}

    if not answer:
        return {"answer": (f"⚠️ The AI provider ({cfg.provider}, model {cfg.model}) returned no "
                           "response. Check the API key is valid and the server can reach the "
                           "provider, then try again."),
                "grounded": False}
    return {"answer": answer, "grounded": True}
