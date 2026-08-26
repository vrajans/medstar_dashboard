"""
ai/groq_client.py  —  Groq API client (free-tier Llama 3.1)
============================================================
Wraps the Groq REST API for:
  • Narrative summaries of KPI data
  • Anomaly detection via LLM reasoning
  • Multi-turn AI chat sessions
  • Tamil (and other language) response support

No GPU, no local model — uses Groq's free-tier Llama 3.1 70B.
Free tier: ~14,400 requests/day (as of 2026).

Setup
-----
  GROQ_API_KEY = from console.groq.com
  GROQ_MODEL   = llama-3.1-70b-versatile (default) or mixtral-8x7b-32768
"""

import os
import json
import logging
import urllib.error
from typing import Optional, Generator

logger = logging.getLogger(__name__)

GROQ_MODEL   = os.getenv("GROQ_MODEL",   "llama-3.3-70b-versatile")
GROQ_BASE    = "https://api.groq.com/openai/v1"

def _get_api_key() -> str:
    """Read key at call time so load_dotenv() in app.py is always honoured."""
    return os.getenv("GROQ_API_KEY", "")

# Keep for backward-compat (e.g. any module that does `from groq_client import GROQ_API_KEY`)
GROQ_API_KEY = _get_api_key()

# Max tokens for different use cases
MAX_TOKENS = {
    "summary":   512,
    "anomaly":   768,
    "chat":     1024,
    "narrative": 800,
}


def _groq_chat(messages: list[dict], model: str = GROQ_MODEL,
               max_tokens: int = 512, temperature: float = 0.3,
               stream: bool = False) -> Optional[str]:
    """
    Chat completion — now routed through the LLM Gateway so every AI feature
    supports multiple providers and per-tenant BYO keys. Falls back to a direct
    Groq call if the gateway is unavailable, preserving the original behavior.

    Provider/model are resolved by the gateway from (in precedence order):
      request tenant context (BYO) → env LLM_PROVIDER/LLM_MODEL → Groq default.
    The `model` arg is honored only when the resolved provider is Groq, so the
    legacy default (GROQ_MODEL) still applies to Groq-routed calls.
    """
    try:
        from ai import llm_gateway
        cfg = llm_gateway.resolve_config()
        # keep legacy model override behavior for Groq-routed calls
        _model = model if cfg.provider == "groq" else None
        reply = llm_gateway.chat(messages, max_tokens=max_tokens,
                                 temperature=temperature, model=_model, config=None)
        if reply is not None:
            return reply
        # gateway returned None → fall through to the direct Groq path below
    except Exception as exc:
        logger.debug("[groq] gateway unavailable, using direct call: %s", exc)

    # ── Direct Groq fallback (original implementation) ──
    api_key = _get_api_key()
    if not api_key:
        logger.warning("[groq] GROQ_API_KEY not set — AI features disabled.")
        return None
    try:
        import urllib.request
        payload = {
            "model":       model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "stream":      False,
        }
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{GROQ_BASE}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
                "User-Agent":    "InsightHub/1.0 (Python urllib)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result  = json.loads(resp.read())
            choices = result.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.error("[groq] HTTP %s — %s", exc.code, body)
        print(f"[groq] HTTP {exc.code} error: {body[:300]}")
    except Exception as exc:
        logger.error("[groq] _groq_chat: %s", exc)
        print(f"[groq] Error: {exc}")
    return None


# ═════════════════════════════════════════════════════════════════════════════
# 1. KPI Narrative Summary
# ═════════════════════════════════════════════════════════════════════════════

def generate_narrative_summary(
    kpi_data:    dict,
    period:      str  = "this month",
    tenant_name: str  = "Your Business",
    language:    str  = "English",   # "English" | "Tamil" | "Hindi"
) -> str:
    """
    Generate a human-readable narrative summary of KPI metrics.

    kpi_data: {
        "sales":     float,
        "purchases": float,
        "margin":    float,
        "bills":     int,
        "top_branch": str,
        "alerts":    [{"level":"danger","msg":"..."}]
    }
    """
    if not _get_api_key():
        return _fallback_narrative(kpi_data, period, tenant_name)

    alerts_text = ""
    if kpi_data.get("alerts"):
        alerts_text = "Active alerts:\n" + "\n".join(
            f"- {a['msg']}" for a in kpi_data["alerts"]
        )

    _fmt = _make_fmt(kpi_data.get("currency", "USD"))

    prompt = f"""You are a business analytics assistant for {tenant_name}.
Write a concise, friendly narrative summary of the following business performance data for {period}.
{"Respond in Tamil language." if language == "Tamil" else
 "Respond in Hindi language." if language == "Hindi" else ""}
Keep it to 3-4 sentences. Focus on key insights and actionable observations.
Don't use bullet points — write it as natural flowing text.

Performance Data:
- Total Sales:     {_fmt(kpi_data.get("sales", 0))}
- Total Purchases: {_fmt(kpi_data.get("purchases", 0))}
- Average Margin:  {kpi_data.get("margin", 0):.1f}%
- Total Bills:     {kpi_data.get("bills", 0):,}
- Top Branch:      {kpi_data.get("top_branch", "N/A")}
{alerts_text}

Write the summary now:"""

    messages = [
        {"role": "system", "content": f"You are a helpful business analytics AI assistant for {tenant_name}."},
        {"role": "user",   "content": prompt},
    ]
    result = _groq_chat(messages, max_tokens=MAX_TOKENS["narrative"], temperature=0.4)
    return result or _fallback_narrative(kpi_data, period, tenant_name)


def _fallback_narrative(kpi_data: dict, period: str, tenant_name: str) -> str:
    """Rule-based fallback when Groq is unavailable."""
    sales   = kpi_data.get("sales", 0)
    margin  = kpi_data.get("margin", 0)
    bills   = kpi_data.get("bills",  0)
    _fmt    = _make_fmt(kpi_data.get("currency", "USD"))

    health = "healthy" if margin >= 20 else "under pressure"
    return (
        f"For {period}, {tenant_name} recorded sales of {_fmt(sales)} "
        f"across {bills:,} transactions. "
        f"Average margin is {margin:.1f}%, which is {health}. "
        + ("Consider reviewing pricing and supplier costs to improve profitability."
           if margin < 20 else "Keep up the great work!")
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. Anomaly Detection
# ═════════════════════════════════════════════════════════════════════════════

def detect_anomalies(
    daily_series: list[dict],   # [{"date":"2026-01-15","sales":45000,"margin":18.2}, ...]
    tenant_name:  str = "Your Business",
) -> list[dict]:
    """
    Use Groq LLM to identify anomalies in a time series.
    Returns list of {"date": ..., "type": ..., "description": ...}
    """
    if not _get_api_key() or not daily_series:
        return _rule_based_anomalies(daily_series)

    # Limit to last 30 data points to stay within context window
    series = daily_series[-30:]
    series_text = "\n".join(
        f"  {d['date']}: sales={d.get('sales',0):,.0f}, margin={d.get('margin',0):.1f}%"
        for d in series
    )

    prompt = f"""Analyse the following daily business data for {tenant_name} and identify any anomalies.
Look for: sudden drops or spikes in sales (>40% deviation from surrounding days),
unusual margin patterns, and gaps in data.

Data:
{series_text}

Respond as a JSON array of objects with fields:
  date (YYYY-MM-DD), type (spike|drop|gap|margin_outlier), description (1 sentence)
Return only the JSON array, no other text. If no anomalies found, return [].
"""

    messages = [
        {"role": "system", "content": "You are a data anomaly detection assistant. Return valid JSON only."},
        {"role": "user",   "content": prompt},
    ]
    result = _groq_chat(messages, max_tokens=MAX_TOKENS["anomaly"], temperature=0.1)
    if result:
        try:
            # Strip any markdown code fences
            clean = result.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            return json.loads(clean)
        except json.JSONDecodeError:
            logger.warning("[groq] anomaly JSON parse failed: %s", result[:200])

    return _rule_based_anomalies(daily_series)


def _rule_based_anomalies(series: list[dict]) -> list[dict]:
    """Fast rule-based anomaly detection as Groq fallback."""
    if not series or len(series) < 3:
        return []

    anomalies = []
    sales_vals = [d.get("sales", 0) for d in series]
    if not any(sales_vals):
        return []

    avg = sum(sales_vals) / len(sales_vals)
    std = (sum((v - avg)**2 for v in sales_vals) / len(sales_vals)) ** 0.5

    for d in series:
        s = d.get("sales", 0)
        z = abs(s - avg) / std if std > 0 else 0
        if z > 2.5:
            atype = "spike" if s > avg else "drop"
            anomalies.append({
                "date":        d.get("date", "?"),
                "type":        atype,
                "description": (
                    f"Sales of ₹{s:,.0f} are {z:.1f}× standard deviations "
                    f"{'above' if s > avg else 'below'} the average."
                ),
            })
    return anomalies


# ═════════════════════════════════════════════════════════════════════════════
# 3. AI Chat (multi-turn)
# ═════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are InsightHub AI, a helpful business analytics assistant.
You help business owners understand their sales, purchases, margins, and inventory data.
You give concise, actionable insights based on the data provided.
Do not make up numbers — only discuss data that is explicitly provided to you.
If you don't have enough data to answer a question, say so clearly.
Keep responses under 200 words unless the user asks for a detailed report."""


def chat(
    user_message:  str,
    history:       list[dict],   # [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]
    context:       str = "",     # injected data context (KPI snapshot, RAG results)
    language:      str = "English",
) -> tuple[str, list[dict]]:
    """
    Multi-turn AI chat.
    Returns (response_text, updated_history).
    history format: list of {"role": "user"|"assistant", "content": "..."}
    """
    if not _get_api_key():
        return ("AI chat is not available. Please set GROQ_API_KEY in your environment variables.",
                history)

    system = SYSTEM_PROMPT
    if language == "Tamil":
        system += " Respond in Tamil language when the user writes in Tamil."
    elif language == "Hindi":
        system += " Respond in Hindi when the user writes in Hindi."

    if context:
        system += f"\n\nCurrent business data context:\n{context}"

    messages = [{"role": "system", "content": system}]
    # Keep last 10 turns to stay within context limits
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_message})

    response = _groq_chat(messages, max_tokens=MAX_TOKENS["chat"], temperature=0.5)
    if not response:
        response = "I'm sorry, I couldn't process your request. Please try again."

    new_history = history + [
        {"role": "user",      "content": user_message},
        {"role": "assistant", "content": response},
    ]
    return response, new_history


def _make_fmt(currency: str = "USD"):
    """Return a value-formatter function for the given currency code."""
    if currency == "INR":
        def _fmt(v):
            if v >= 1e7: return f"₹{v/1e7:.2f}Cr"
            if v >= 1e5: return f"₹{v/1e5:.2f}L"
            if v >= 1e3: return f"₹{v/1e3:.1f}K"
            return f"₹{v:.0f}"
    else:  # USD / default
        def _fmt(v):
            if v >= 1e6: return f"${v/1e6:.2f}M"
            if v >= 1e3: return f"${v/1e3:.1f}K"
            return f"${v:.0f}"
    return _fmt


def build_kpi_context(kpi_data: dict, rag_results: list[str] = None) -> str:
    """Build a context string to inject into the chat system prompt."""
    _fmt = _make_fmt(kpi_data.get("currency", "USD"))

    lines = [
        f"Sales (current period):  {_fmt(kpi_data.get('sales', 0))}",
        f"Purchases:               {_fmt(kpi_data.get('purchases', 0))}",
        f"Average Margin:          {kpi_data.get('margin', 0):.1f}%",
        f"Total Bills:             {kpi_data.get('bills', 0):,}",
        f"Top Branch:              {kpi_data.get('top_branch', 'N/A')}",
    ]
    if kpi_data.get("alerts"):
        lines.append("Active Alerts:")
        for a in kpi_data["alerts"]:
            lines.append(f"  - {a['msg']}")

    if rag_results:
        lines.append("\nRelated data from your records:")
        for r in rag_results[:3]:
            lines.append(f"  • {r}")

    return "\n".join(lines)
