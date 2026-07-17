"""
ai/agents/router.py  --  Router Agent (Intent Classifier + Agent Dispatcher)
=============================================================================

User Story (Phase 1)
--------------------
AS A business user
I WANT the AI to automatically understand what kind of question I'm asking
SO THAT I get the most accurate, structured answer without choosing an agent myself

Acceptance Criteria
-------------------
- AC1: Simple "what was our revenue last month?" → routes to Analytics Agent
- AC2: "Why did margin drop in April?" → routes to Insight Agent (ReAct loop)
- AC3: "Predict next quarter sales" → routes to Forecast Agent
- AC4: Upload a .csv file → routes to Schema Agent first, then Analytics
- AC5: "Check my data for errors" → routes to Data Quality Agent
- AC6: Router shows its decision in the UI trace (transparent reasoning)
- AC7: Falls back to Analytics Agent if intent is ambiguous
- AC8: Classification latency < 800ms (uses fast Groq call, not full 70B)

Architecture
------------
  User Message + File (optional)
       |
  RouterAgent.route()
       |-- classify_intent()  →  Groq (fast, low-token prompt)
       |       Returns: {agent, confidence, reason}
       |
       +--> AnalyticsAgent   (metrics, KPIs, totals, comparisons)
       +--> InsightAgent     (why/how/root cause, multi-step investigation)
       +--> ForecastAgent    (predict, next quarter, trend, projection)
       +--> SchemaAgent      (file upload, column detection, format mapping)
       +--> QualityAgent     (errors, missing data, anomalies in upload)
"""

import logging
import os
import json
import re
from typing import Optional

import pandas as pd

from ai.agents import AgentResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent definitions — used for keyword pre-filter + Groq confirmation
# ---------------------------------------------------------------------------

_INTENT_MAP = {
    "analytics": {
        "keywords": [
            "how much", "total", "revenue", "sales", "margin", "top",
            "average", "count", "summary", "report", "performance",
            "compare", "vs", "versus", "ratio", "breakdown", "kpi",
            "cash", "credit", "branch", "supplier", "monthly", "weekly",
            "daily", "last month", "this week", "ytd", "yesterday",
        ],
        "description": "Factual metrics, KPIs, totals, comparisons from existing data",
    },
    "insight": {
        "keywords": [
            "why", "reason", "cause", "explain", "investigate", "root cause",
            "drop", "spike", "decline", "increase", "unusual", "what happened",
            "how did", "impact", "affect", "correlation", "pattern",
        ],
        "description": "Root cause analysis, explanation, multi-step investigation",
    },
    "forecast": {
        "keywords": [
            "predict", "forecast", "next", "future", "project", "projection",
            "expect", "estimate", "trend", "will", "quarter", "90 day",
            "30 day", "next month", "next year", "growth rate",
        ],
        "description": "Predictions, trend extrapolation, future projections",
    },
    "schema": {
        "keywords": [
            "upload", "file", "csv", "excel", "xlsx", "quickbooks", "square",
            "shopify", "marg", "columns", "format", "map", "mapping",
            "detect", "identify", "schema", "structure", "fields",
        ],
        "description": "File upload processing, column detection, format mapping",
    },
    "quality": {
        "keywords": [
            "error", "invalid", "missing", "null", "duplicate", "clean",
            "validate", "check data", "data quality", "bad data",
            "corrupted", "wrong format", "fix", "anomaly in data",
            "are there errors", "errors in", "errors in my", "check my data",
            "quality score", "data issues", "missing values", "missing data",
            "fix my data", "data errors", "bad rows", "null values",
        ],
        "description": "Data validation, quality check, anomaly detection in upload",
    },
}

_GROQ_CLASSIFY_PROMPT = """You are a routing agent for an analytics platform.
Classify the user's question into exactly ONE of these agents:

- analytics   : factual metrics, KPIs, totals, comparisons ("how much", "top X", "what is our revenue")
- insight     : root cause analysis, explanation, investigation ("why did", "what caused", "explain the drop")
- forecast    : predictions, projections, future trends ("predict", "next quarter", "will sales grow")
- schema      : file upload, column mapping, format detection ("I uploaded a file", "QuickBooks CSV", "map these columns")
- quality     : data validation, error detection in uploaded data ("check my data", "are there errors", "missing values")

Respond ONLY with valid JSON:
{"agent": "<one of the 5 agents>", "confidence": <0.0-1.0>, "reason": "<one sentence>"}

User message: {question}
"""


class RouterAgent:
    """
    Classifies user intent and dispatches to the correct specialist agent.

    Falls back to 'analytics' when confidence < 0.6 or Groq is unavailable.
    """

    def __init__(self, memory=None):
        """
        memory: AgentMemory instance (optional — for logging routing decisions)
        """
        self.memory = memory

    def route(
        self,
        question: str,
        sales_df: Optional[pd.DataFrame] = None,
        purchase_df: Optional[pd.DataFrame] = None,
        kpi_data: Optional[dict] = None,
        tenant_id: int = 0,
        language: str = "English",
        history: Optional[list] = None,
        uploaded_file_info: Optional[dict] = None,
    ) -> AgentResult:
        """
        Main entry point. Classify the question, run the right agent,
        return a unified AgentResult.

        uploaded_file_info: {"filename": str, "columns": list[str], "df": DataFrame}
                            — set when user just uploaded a file
        """
        # Step 1: Fast keyword pre-filter
        agent_name, keyword_confidence = self._keyword_classify(question)

        # Step 2: If file was uploaded, always route to schema first
        if uploaded_file_info and uploaded_file_info.get("columns"):
            agent_name = "schema"
            keyword_confidence = 1.0
            reason = "File upload detected — routing to Schema Agent"
        elif keyword_confidence >= 0.75:
            reason = f"Keyword match ({keyword_confidence:.0%} confidence)"
        else:
            # Step 3: Use Groq for ambiguous cases
            agent_name, keyword_confidence, reason = self._groq_classify(question, agent_name)

        logger.info("[Router] → %s (conf=%.2f) reason=%s", agent_name, keyword_confidence, reason)

        # Step 4: Run the selected agent
        routing_step = {
            "step": 0,
            "thought": f"Question classified as '{agent_name}' intent",
            "action":  f"Routing to {agent_name.title()} Agent",
            "observation": reason,
        }

        result = self._dispatch(
            agent_name=agent_name,
            question=question,
            sales_df=sales_df,
            purchase_df=purchase_df,
            kpi_data=kpi_data,
            tenant_id=tenant_id,
            language=language,
            history=history or [],
            uploaded_file_info=uploaded_file_info,
        )

        # Prepend routing step to the result's reasoning trace
        result.reasoning_steps = [routing_step] + result.reasoning_steps
        return result

    # -------------------------------------------------------------------------
    # Keyword classifier (no LLM — instant)
    # -------------------------------------------------------------------------

    def _keyword_classify(self, question: str) -> tuple[str, float]:
        """Score each agent by keyword matches. Return (best_agent, confidence)."""
        q_lower = question.lower()
        scores: dict[str, int] = {agent: 0 for agent in _INTENT_MAP}

        for agent, info in _INTENT_MAP.items():
            for kw in info["keywords"]:
                if kw in q_lower:
                    scores[agent] += 1

        total = sum(scores.values()) or 1
        best  = max(scores, key=lambda a: scores[a])
        conf  = scores[best] / total if scores[best] > 0 else 0.0

        # Normalize confidence: 1 match in analytics = 0.4, 3+ matches = 0.9+
        if scores[best] == 0:
            return "analytics", 0.3          # default fallback
        if scores[best] == 1:
            return best, min(0.65, conf + 0.3)
        return best, min(0.95, conf + 0.5)

    # -------------------------------------------------------------------------
    # Groq classifier (LLM — used for ambiguous cases)
    # -------------------------------------------------------------------------

    def _groq_classify(
        self, question: str, fallback: str
    ) -> tuple[str, float, str]:
        """Ask Groq to classify intent. Returns (agent, confidence, reason)."""
        try:
            from groq import Groq as _GroqClient

            api_key = os.environ.get("GROQ_API_KEY", "")
            if not api_key:
                return fallback, 0.5, "No GROQ_API_KEY — using keyword fallback"

            client   = _GroqClient(api_key=api_key)
            prompt   = _GROQ_CLASSIFY_PROMPT.format(question=question[:500])
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",   # fast 8B for routing, not 70B
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
                temperature=0.0,
            )
            raw = response.choices[0].message.content.strip()

            # Extract JSON even if wrapped in markdown
            m = re.search(r'\{.*?\}', raw, re.DOTALL)
            if m:
                parsed = json.loads(m.group())
                agent  = parsed.get("agent", fallback)
                if agent not in _INTENT_MAP:
                    agent = fallback
                return (
                    agent,
                    float(parsed.get("confidence", 0.7)),
                    parsed.get("reason", "Groq classification"),
                )

        except Exception as exc:
            logger.warning("[Router] Groq classify failed: %s", exc)

        return fallback, 0.5, "Groq unavailable — keyword fallback"

    # -------------------------------------------------------------------------
    # Dispatcher
    # -------------------------------------------------------------------------

    def _dispatch(
        self,
        agent_name: str,
        question: str,
        sales_df: Optional[pd.DataFrame],
        purchase_df: Optional[pd.DataFrame],
        kpi_data: Optional[dict],
        tenant_id: int,
        language: str,
        history: list,
        uploaded_file_info: Optional[dict],
    ) -> AgentResult:
        """Instantiate + call the correct agent."""
        common_kwargs = dict(
            question=question,
            tenant_id=tenant_id,
            language=language,
            history=history,
            memory=self.memory,
        )

        try:
            if agent_name == "schema":
                from ai.agents.schema_agent import SchemaAgent
                return SchemaAgent(**{k: v for k, v in common_kwargs.items()
                                      if k in ("memory",)}).run(
                    question=question,
                    tenant_id=tenant_id,
                    language=language,
                    uploaded_file_info=uploaded_file_info,
                    sales_df=sales_df,
                    purchase_df=purchase_df,
                )

            elif agent_name == "insight":
                from ai.agents.insight_agent import InsightAgent
                return InsightAgent(memory=self.memory).run(
                    question=question,
                    sales_df=sales_df,
                    purchase_df=purchase_df,
                    kpi_data=kpi_data,
                    tenant_id=tenant_id,
                    language=language,
                    history=history,
                )

            elif agent_name == "forecast":
                from ai.agents.forecast_agent import ForecastAgent
                return ForecastAgent(memory=self.memory).run(
                    question=question,
                    sales_df=sales_df,
                    purchase_df=purchase_df,
                    kpi_data=kpi_data,
                    tenant_id=tenant_id,
                    language=language,
                )

            elif agent_name == "quality":
                from ai.agents.quality_agent import QualityAgent
                return QualityAgent(memory=self.memory).run(
                    question=question,
                    sales_df=sales_df,
                    purchase_df=purchase_df,
                    tenant_id=tenant_id,
                    language=language,
                )

            else:  # analytics (default)
                from ai.agents.analytics_agent import AnalyticsAgent
                return AnalyticsAgent(memory=self.memory).run(
                    question=question,
                    sales_df=sales_df,
                    purchase_df=purchase_df,
                    kpi_data=kpi_data,
                    tenant_id=tenant_id,
                    language=language,
                    history=history,
                )

        except Exception as exc:
            logger.error("[Router] Dispatch error for agent '%s': %s", agent_name, exc, exc_info=True)
            return AgentResult(
                answer=f"I encountered an error while processing your question: {exc}",
                agent=agent_name,
                error=str(exc),
                confidence=0.0,
            )
