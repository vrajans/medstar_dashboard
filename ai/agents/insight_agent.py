"""
ai/agents/insight_agent.py  --  Insight Agent (ReAct Multi-Step Reasoning)
============================================================================

User Story (Phase 1 — Key differentiator)
-------------------------------------------
AS A business owner investigating a business problem
I WANT the AI to show me its step-by-step reasoning as it investigates
SO THAT I trust the answer and understand what happened in my business

Acceptance Criteria
-------------------
- AC1: "Why did margin drop in April?" triggers 3-5 visible reasoning steps
- AC2: Each step shows: THOUGHT → ACTION → OBSERVATION (ReAct pattern)
- AC3: Agent uses real data tools: compare_periods(), top_by_metric(),
       supplier_analysis(), branch_comparison()
- AC4: Reasoning trace appears in the UI as expandable step cards
- AC5: Final answer explicitly states the root cause with supporting numbers
- AC6: If investigation reveals multiple causes, ranks them by impact
- AC7: Max 5 reasoning steps to keep response time < 10 seconds

ReAct Loop
----------
1. THOUGHT: "I need to check if the margin drop is sales-side or cost-side"
2. ACTION:  compare_periods(metric="margin", period1="2025-03", period2="2025-04")
3. OBSERVE: "Margin dropped from 28.3% → 21.1% — that's 7.2 percentage points"
4. THOUGHT: "Now I need to check if supplier costs increased in April"
5. ACTION:  top_by_metric(df=purchase_df, metric="net_amount", month="2025-04")
6. OBSERVE: "Pharma Dist Ltd purchases jumped 340% in April — likely cause"
7. ANSWER:  "Margin dropped because Pharma Dist Ltd raised prices..."
"""

import logging
import os
import json
import re
from typing import Optional

import pandas as pd

from ai.agents import AgentResult

logger = logging.getLogger(__name__)

_MAX_STEPS = 5

# ---------------------------------------------------------------------------
# Tool registry — functions the Insight Agent can call
# ---------------------------------------------------------------------------

def _compare_periods(
    df: Optional[pd.DataFrame],
    metric: str,
    date_col: str,
    period1: str,
    period2: str,
) -> str:
    """Compare a metric between two months (YYYY-MM)."""
    if df is None or df.empty or date_col not in df.columns:
        return "No data available for period comparison"

    try:
        d = df.copy()
        d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
        d["_month"] = d[date_col].dt.to_period("M").astype(str)

        if metric not in d.columns:
            return f"Column '{metric}' not found"

        def _agg(period: str) -> float:
            subset = d[d["_month"] == period]
            if subset.empty:
                return 0.0
            return float(subset[metric].mean() if "pct" in metric else subset[metric].sum())

        v1, v2 = _agg(period1), _agg(period2)
        delta   = v2 - v1
        pct     = ((v2 - v1) / v1 * 100) if v1 != 0 else 0

        direction = "increased" if delta > 0 else "decreased"
        sym       = "%" if "pct" in metric else ""
        return (
            f"{metric}: {period1}={v1:.2f}{sym} → {period2}={v2:.2f}{sym} "
            f"({direction} by {abs(delta):.2f}{sym}, {pct:+.1f}%)"
        )
    except Exception as exc:
        return f"Period comparison error: {exc}"


def _top_by_metric(
    df: Optional[pd.DataFrame],
    group_col: str,
    value_col: str,
    top_n: int = 5,
    filter_month: Optional[str] = None,
    date_col: str = "bill_date",
) -> str:
    """Return top N items by a metric, optionally filtered to a month."""
    if df is None or df.empty:
        return "No data available"

    try:
        d = df.copy()
        if filter_month and date_col in d.columns:
            d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
            d["_month"] = d[date_col].dt.to_period("M").astype(str)
            d = d[d["_month"] == filter_month]

        if group_col not in d.columns or value_col not in d.columns:
            available = ", ".join(d.columns[:8])
            return f"Columns '{group_col}' or '{value_col}' not found. Available: {available}"

        result = (
            d.groupby(group_col)[value_col]
            .sum()
            .sort_values(ascending=False)
            .head(top_n)
        )
        rows = [f"  {i+1}. {name}: {val:,.0f}" for i, (name, val) in enumerate(result.items())]
        period_note = f" (month={filter_month})" if filter_month else ""
        return f"Top {top_n} {group_col} by {value_col}{period_note}:\n" + "\n".join(rows)
    except Exception as exc:
        return f"Top-by-metric error: {exc}"


def _monthly_trend(
    df: Optional[pd.DataFrame],
    value_col: str,
    date_col: str = "bill_date",
    months: int = 6,
) -> str:
    """Return monthly trend for a metric."""
    if df is None or df.empty or date_col not in df.columns:
        return "No data for trend analysis"

    try:
        d = df.copy()
        d[date_col]  = pd.to_datetime(d[date_col], errors="coerce")
        d["_month"]  = d[date_col].dt.to_period("M")
        agg_fn       = "mean" if "pct" in value_col else "sum"
        trend        = (
            d.groupby("_month")[value_col]
            .agg(agg_fn)
            .sort_index(ascending=False)
            .head(months)
        )
        sym  = "%" if "pct" in value_col else ""
        rows = [f"  {str(m)}: {v:.2f}{sym}" for m, v in trend.items()]
        return f"{value_col} monthly trend (last {months} months):\n" + "\n".join(rows)
    except Exception as exc:
        return f"Trend error: {exc}"


def _data_summary(
    sales_df: Optional[pd.DataFrame],
    purchase_df: Optional[pd.DataFrame],
) -> str:
    """Quick summary of available data."""
    parts = []
    if sales_df is not None and not sales_df.empty:
        cols = ", ".join(sales_df.columns[:10])
        parts.append(f"Sales data: {len(sales_df)} rows, columns: {cols}")
    else:
        parts.append("Sales data: empty")

    if purchase_df is not None and not purchase_df.empty:
        cols = ", ".join(purchase_df.columns[:10])
        parts.append(f"Purchase data: {len(purchase_df)} rows, columns: {cols}")
    else:
        parts.append("Purchase data: empty")

    return "\n".join(parts)


_TOOLS = {
    "compare_periods":  _compare_periods,
    "top_by_metric":    _top_by_metric,
    "monthly_trend":    _monthly_trend,
    "data_summary":     _data_summary,
}

# ---------------------------------------------------------------------------
# Groq prompt for ReAct loop
# ---------------------------------------------------------------------------

_REACT_SYSTEM = """You are InsightHub's Insight Agent — a business intelligence investigator.
Your job is to do a MULTI-STEP investigation to answer "why" and "how" questions.

You have access to these tools (call them by returning JSON):
  compare_periods(metric, date_col, period1, period2)  -- compare metric between two months
  top_by_metric(group_col, value_col, top_n, filter_month, date_col) -- top N by value
  monthly_trend(value_col, date_col, months)           -- see trend over months
  data_summary()                                       -- list available columns

Data context:
{data_context}

ReAct format — each step must be:
THOUGHT: <what you need to find out and why>
ACTION: <tool_name> {{"param": "value", ...}}
OBSERVATION: <what the tool returned>
...repeat up to 5 steps...
FINAL_ANSWER: <root cause with specific numbers from the data>

Rules:
- Use real numbers from the data context, do not hallucinate
- Each investigation must use at least 2 tool calls
- State the root cause clearly with supporting evidence
- If you can't find the answer, say exactly what data is missing
"""

_REACT_USER = """Question: {question}

Start your investigation. Use the ReAct format. Max {max_steps} steps.
Return valid JSON tool calls in your ACTION lines."""


class InsightAgent:
    """
    Multi-step ReAct reasoning agent for root-cause investigation.

    The visible reasoning trace is the core product differentiator:
    users see exactly HOW the AI arrived at its answer, building trust.
    """

    def __init__(self, memory=None):
        self.memory = memory

    def run(
        self,
        question: str,
        sales_df: Optional[pd.DataFrame] = None,
        purchase_df: Optional[pd.DataFrame] = None,
        kpi_data: Optional[dict] = None,
        tenant_id: int = 0,
        language: str = "English",
        history: Optional[list] = None,
    ) -> AgentResult:
        steps: list[dict] = []

        # Build compact data context for Groq
        from ai.rag import build_data_context
        data_context = build_data_context(sales_df, purchase_df,
                                           max_sales_rows=30, max_purchase_rows=15)

        # Run ReAct loop
        try:
            answer, steps = self._react_loop(
                question=question,
                data_context=data_context,
                sales_df=sales_df,
                purchase_df=purchase_df,
                language=language,
            )
        except Exception as exc:
            logger.error("[InsightAgent] ReAct loop error: %s", exc)
            answer = f"Investigation failed: {exc}"

        # Save to memory
        if self.memory and "failed" not in answer:
            try:
                self.memory.save_insight(
                    tenant_id=tenant_id,
                    question=question,
                    agent="insight",
                    answer=answer,
                    reasoning_steps=steps,
                    kpi_snapshot=kpi_data or {},
                    language=language,
                )
            except Exception:
                pass

        return AgentResult(
            answer=answer,
            agent="insight",
            reasoning_steps=steps,
            confidence=0.85,
            sources=["sales_df", "purchase_df", "react_loop", "groq_llama3.1"],
        )

    def _react_loop(
        self,
        question: str,
        data_context: str,
        sales_df: Optional[pd.DataFrame],
        purchase_df: Optional[pd.DataFrame],
        language: str,
    ) -> tuple[str, list[dict]]:
        """
        Execute the ReAct loop:
        1. Send question + data context to Groq
        2. Parse THOUGHT/ACTION/OBSERVATION blocks
        3. Execute tool calls against real DataFrames
        4. Feed observations back to Groq
        5. Extract FINAL_ANSWER
        """
        steps: list[dict] = []

        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            # Offline fallback — use local tool calls only
            return self._offline_investigation(question, sales_df, purchase_df, steps)

        try:
            from groq import Groq as _GroqClient

            client = _GroqClient(api_key=api_key)
            messages = [
                {
                    "role": "system",
                    "content": _REACT_SYSTEM.format(data_context=data_context[:6000]),
                },
                {
                    "role": "user",
                    "content": _REACT_USER.format(question=question, max_steps=_MAX_STEPS),
                },
            ]

            full_trace = ""
            step_num   = 1

            for iteration in range(_MAX_STEPS):
                response  = client.chat.completions.create(
                    model="llama-3.1-70b-versatile",
                    messages=messages,
                    max_tokens=800,
                    temperature=0.1,
                    stop=["OBSERVATION:"],  # let us inject observations
                )
                chunk = response.choices[0].message.content.strip()
                full_trace += chunk + "\n"

                # Parse THOUGHT + ACTION from this chunk
                thought = _extract_block(chunk, "THOUGHT")
                action  = _extract_block(chunk, "ACTION")
                answer  = _extract_block(chunk, "FINAL_ANSWER")

                if answer:
                    steps.append({
                        "step":        step_num,
                        "thought":     thought or "Synthesizing findings",
                        "action":      "FINAL_ANSWER",
                        "observation": answer,
                    })
                    if language != "English":
                        answer = self._translate(answer, language)
                    return answer, steps

                # Execute tool call
                observation = self._execute_tool(action, sales_df, purchase_df)

                steps.append({
                    "step":        step_num,
                    "thought":     thought or "(investigating...)",
                    "action":      action or "(no action)",
                    "observation": observation,
                })
                step_num += 1

                # Feed observation back
                messages.append({"role": "assistant", "content": chunk})
                messages.append({
                    "role": "user",
                    "content": f"OBSERVATION: {observation}\n\nContinue the investigation.",
                })

            # Max steps reached — ask for summary
            messages.append({"role": "user",
                             "content": "You've used your maximum steps. State your FINAL_ANSWER now."})
            final = client.chat.completions.create(
                model="llama-3.1-70b-versatile",
                messages=messages,
                max_tokens=400,
                temperature=0.1,
            )
            answer = _extract_block(final.choices[0].message.content, "FINAL_ANSWER") \
                     or final.choices[0].message.content.strip()

            if language != "English":
                answer = self._translate(answer, language)

            return answer, steps

        except Exception as exc:
            logger.error("[InsightAgent] Groq ReAct error: %s", exc)
            return self._offline_investigation(question, sales_df, purchase_df, steps)

    def _execute_tool(
        self,
        action_text: str,
        sales_df: Optional[pd.DataFrame],
        purchase_df: Optional[pd.DataFrame],
    ) -> str:
        """Parse action text and execute the appropriate tool."""
        if not action_text:
            return "No action specified"

        action_lower = action_text.lower()

        # Extract JSON params from action text
        params: dict = {}
        m = re.search(r'\{.*?\}', action_text, re.DOTALL)
        if m:
            try:
                params = json.loads(m.group())
            except Exception:
                pass

        if "compare_periods" in action_lower:
            return _compare_periods(
                df=sales_df,
                metric=params.get("metric", "net_amount"),
                date_col=params.get("date_col", "bill_date"),
                period1=params.get("period1", ""),
                period2=params.get("period2", ""),
            )

        elif "top_by_metric" in action_lower:
            df = purchase_df if params.get("data") == "purchases" else sales_df
            return _top_by_metric(
                df=df,
                group_col=params.get("group_col", "branch"),
                value_col=params.get("value_col", "net_amount"),
                top_n=int(params.get("top_n", 5)),
                filter_month=params.get("filter_month"),
                date_col=params.get("date_col", "bill_date"),
            )

        elif "monthly_trend" in action_lower:
            df = purchase_df if params.get("data") == "purchases" else sales_df
            return _monthly_trend(
                df=df,
                value_col=params.get("value_col", "net_amount"),
                date_col=params.get("date_col", "bill_date"),
                months=int(params.get("months", 6)),
            )

        elif "data_summary" in action_lower:
            return _data_summary(sales_df, purchase_df)

        return f"Unknown tool in: {action_text[:100]}"

    def _offline_investigation(
        self,
        question: str,
        sales_df: Optional[pd.DataFrame],
        purchase_df: Optional[pd.DataFrame],
        steps: list[dict],
    ) -> tuple[str, list[dict]]:
        """Fallback investigation using only local tools (no Groq)."""
        steps.append({
            "step": 1,
            "thought": "Groq unavailable — running local data investigation",
            "action": "data_summary()",
            "observation": _data_summary(sales_df, purchase_df),
        })

        # Try a generic trend analysis
        obs = _monthly_trend(sales_df, "net_amount", months=6)
        steps.append({
            "step": 2,
            "thought": "Checking recent sales trend for anomalies",
            "action": "monthly_trend(value_col='net_amount', months=6)",
            "observation": obs,
        })

        if purchase_df is not None and not purchase_df.empty:
            obs2 = _top_by_metric(purchase_df, "supplier_name", "net_amount", top_n=5)
            steps.append({
                "step": 3,
                "thought": "Checking top suppliers by purchase value",
                "action": "top_by_metric(group_col='supplier_name', value_col='net_amount')",
                "observation": obs2,
            })

        answer = (
            "I've completed a local data investigation. "
            "AI-powered root cause analysis requires a GROQ_API_KEY — "
            "please add it to your .env file to get full 'why' explanations. "
            "The data summary above shows current trends."
        )
        return answer, steps

    def _translate(self, text: str, language: str) -> str:
        """Best-effort translation via Groq."""
        try:
            api_key = os.environ.get("GROQ_API_KEY", "")
            if not api_key:
                return text
            from groq import Groq as _GroqClient
            client   = _GroqClient(api_key=api_key)
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{
                    "role": "user",
                    "content": f"Translate to {language} (keep numbers and business terms):\n{text}"
                }],
                max_tokens=600,
                temperature=0.1,
            )
            return response.choices[0].message.content.strip()
        except Exception:
            return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_block(text: str, label: str) -> str:
    """Extract the content after a ReAct label like 'THOUGHT:' or 'FINAL_ANSWER:'."""
    pattern = rf"{label}:?\s*(.*?)(?=\n(?:THOUGHT|ACTION|OBSERVATION|FINAL_ANSWER):|$)"
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""
