"""
ai/agents/forecast_agent.py  --  Forecast Agent (Trend Projection)
====================================================================

User Story (Phase 2)
---------------------
AS A business owner planning for growth
I WANT the AI to predict my sales and margin for the next 30/90 days
SO THAT I can make better inventory and staffing decisions

Acceptance Criteria
-------------------
- AC1: 30-day and 90-day revenue projections using linear trend
- AC2: Seasonal adjustment if data covers 12+ months
- AC3: Shows projection table: month, predicted sales, upper/lower bounds
- AC4: Explains the trend basis ("based on 20% month-over-month growth")
- AC5: Uses Groq to add business narrative around the numbers
- AC6: Flags if recent trend is volatile (confidence < 60%)
- AC7: Works even without Groq — pure Pandas projection with narrative template
"""

import logging
import os
from typing import Optional

import pandas as pd
import numpy as np

from ai.agents import AgentResult

logger = logging.getLogger(__name__)


class ForecastAgent:
    """
    Trend-based sales forecasting agent.

    Uses linear regression on monthly sales data for projection,
    then Groq for business narrative around the numbers.
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
    ) -> AgentResult:
        steps: list[dict] = []

        if sales_df is None or sales_df.empty:
            return AgentResult(
                answer="No sales data available for forecasting. Please upload your sales data first.",
                agent="forecast",
                reasoning_steps=[],
                confidence=0.0,
                error="no_data",
            )

        # Step 1: Build monthly series
        monthly, steps = self._build_monthly_series(sales_df, steps)

        if monthly is None or len(monthly) < 3:
            return AgentResult(
                answer="Need at least 3 months of data for forecasting. "
                       f"Currently have {len(monthly) if monthly is not None else 0} months.",
                agent="forecast",
                reasoning_steps=steps,
                confidence=0.0,
                error="insufficient_data",
            )

        # Step 2: Linear regression projection
        projection, confidence, trend_desc, steps = self._project(monthly, steps)

        # Step 3: Format projection table
        table_text = self._format_table(projection)

        # Step 4: Groq narrative
        answer = self._groq_narrative(
            question=question,
            monthly=monthly,
            projection=projection,
            trend_desc=trend_desc,
            confidence=confidence,
            table_text=table_text,
            language=language,
        )

        steps.append({
            "step": len(steps) + 1,
            "thought": "Combining projection data with AI narrative",
            "action": "groq_narrative(projection, trend)",
            "observation": f"Forecast ready: {len(projection)} months ahead, confidence={confidence:.0%}",
        })

        return AgentResult(
            answer=answer,
            agent="forecast",
            reasoning_steps=steps,
            confidence=confidence,
            sources=["sales_df", "linear_regression", "groq_llama3.1"],
            metadata={
                "projection": projection,
                "trend_desc": trend_desc,
                "monthly_history": monthly.to_dict() if monthly is not None else {},
            },
        )

    def _build_monthly_series(
        self,
        sales_df: pd.DataFrame,
        steps: list[dict],
    ) -> tuple[Optional[pd.Series], list[dict]]:
        """Aggregate sales to monthly totals."""
        try:
            d = sales_df.copy()
            d["bill_date"] = pd.to_datetime(d["bill_date"], errors="coerce")
            d = d.dropna(subset=["bill_date"])
            d["month"] = d["bill_date"].dt.to_period("M")
            monthly = d.groupby("month")["net_amount"].sum().sort_index()

            steps.append({
                "step": 1,
                "thought": "Aggregating sales data to monthly totals for trend analysis",
                "action": "groupby(month)[net_amount].sum()",
                "observation": (
                    f"Monthly series: {len(monthly)} months | "
                    f"Range: {monthly.index[0]} → {monthly.index[-1]} | "
                    f"Avg: ${monthly.mean():,.0f}/month"
                ),
            })
            return monthly, steps
        except Exception as exc:
            logger.error("[ForecastAgent] Build series error: %s", exc)
            steps.append({
                "step": 1,
                "thought": "Error building monthly series",
                "action": "groupby(month)",
                "observation": str(exc),
            })
            return None, steps

    def _project(
        self,
        monthly: pd.Series,
        steps: list[dict],
        horizon_months: int = 3,
    ) -> tuple[list[dict], float, str, list[dict]]:
        """
        Linear regression projection.

        Returns (projection_list, confidence, trend_description, steps).
        projection_list: [{"month": "2026-07", "predicted": 123456, "low": ..., "high": ...}]
        """
        try:
            vals  = monthly.values.astype(float)
            n     = len(vals)
            x     = np.arange(n)

            # Linear fit
            coeffs = np.polyfit(x, vals, deg=1)
            slope  = coeffs[0]
            intercept = coeffs[1]

            # R² as confidence proxy
            fitted = np.polyval(coeffs, x)
            ss_res = np.sum((vals - fitted) ** 2)
            ss_tot = np.sum((vals - vals.mean()) ** 2)
            r2     = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            conf   = max(0.3, min(0.95, r2))

            # Residual std for confidence interval
            residuals = vals - fitted
            std_err   = float(np.std(residuals)) * 1.5

            # Trend description
            avg         = float(vals.mean())
            growth_rate = (slope / avg * 100) if avg > 0 else 0
            if abs(growth_rate) < 1:
                trend_desc = "stable with minimal month-over-month change"
            elif growth_rate > 0:
                trend_desc = f"growing at ~{growth_rate:.1f}% per month"
            else:
                trend_desc = f"declining at ~{abs(growth_rate):.1f}% per month"

            steps.append({
                "step": 2,
                "thought": "Fitting linear trend to historical monthly sales",
                "action": f"np.polyfit(x, sales, deg=1) → slope={slope:+,.0f}",
                "observation": (
                    f"Trend is {trend_desc}. "
                    f"R²={r2:.2f} → forecast confidence={conf:.0%}. "
                    f"Residual std dev: ±${std_err:,.0f}/month"
                ),
            })

            # Project forward
            last_period = monthly.index[-1]
            projection  = []
            for i in range(1, horizon_months + 1):
                future_x   = n - 1 + i
                predicted  = float(np.polyval(coeffs, future_x))
                predicted  = max(0, predicted)
                low        = max(0, predicted - std_err)
                high       = predicted + std_err
                month_str  = str(last_period + i)
                projection.append({
                    "month":     month_str,
                    "predicted": round(predicted),
                    "low":       round(low),
                    "high":      round(high),
                })

            steps.append({
                "step": 3,
                "thought": f"Projecting {horizon_months} months ahead from {str(last_period)}",
                "action": f"polyval(coeffs, x+{n}) for x in 1..{horizon_months}",
                "observation": " | ".join(
                    f"{p['month']}: ${p['predicted']:,.0f} (±${p['high']-p['predicted']:,.0f})"
                    for p in projection
                ),
            })

            return projection, conf, trend_desc, steps

        except Exception as exc:
            logger.error("[ForecastAgent] Projection error: %s", exc)
            return [], 0.0, "unknown", steps

    def _format_table(self, projection: list[dict]) -> str:
        if not projection:
            return "No projection data"
        lines = ["Month       | Predicted    | Low          | High"]
        lines.append("-" * 52)
        for p in projection:
            lines.append(
                f"{p['month']}  | ${p['predicted']:>10,.0f} | ${p['low']:>10,.0f} | ${p['high']:>10,.0f}"
            )
        return "\n".join(lines)

    def _groq_narrative(
        self,
        question: str,
        monthly: pd.Series,
        projection: list[dict],
        trend_desc: str,
        confidence: float,
        table_text: str,
        language: str,
    ) -> str:
        """Wrap projection table with business narrative via Groq."""
        recent_months = monthly.tail(6)
        history_text  = "\n".join(
            f"  {str(m)}: ${v:,.0f}" for m, v in recent_months.items()
        )

        prompt = f"""You are a business analytics AI.

Historical monthly sales (last {len(recent_months)} months):
{history_text}

Sales trend: {trend_desc}
Forecast confidence: {confidence:.0%}

Projection table:
{table_text}

User question: {question}

Write a concise 3-4 sentence business forecast response:
1. State the trend clearly with specific numbers
2. Present the forecast table (include it verbatim)
3. Note the confidence level and what could change it
4. Give one actionable business recommendation

Keep it business-friendly. Respond in {language}."""

        try:
            api_key = os.environ.get("GROQ_API_KEY", "")
            if not api_key:
                raise ValueError("No GROQ_API_KEY")

            from groq import Groq as _GroqClient
            client   = _GroqClient(api_key=api_key)
            response = client.chat.completions.create(
                model="llama-3.1-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()

        except Exception as exc:
            logger.warning("[ForecastAgent] Groq narrative failed: %s", exc)
            # Local fallback narrative
            proj_str = " | ".join(
                f"{p['month']}: ${p['predicted']:,.0f}" for p in projection
            )
            return (
                f"Sales are currently **{trend_desc}**.\n\n"
                f"**30/90-Day Forecast:**\n```\n{table_text}\n```\n\n"
                f"Forecast confidence: **{confidence:.0%}** "
                f"({'high' if confidence >= 0.75 else 'moderate' if confidence >= 0.5 else 'low'}).\n\n"
                f"_Tip: More historical data improves forecast accuracy._"
            )
