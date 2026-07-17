"""
ai/agents/quality_agent.py  --  Data Quality Agent
====================================================

User Story (Phase 1 — Runs automatically on every upload)
-----------------------------------------------------------
AS A business owner uploading data
I WANT the system to automatically validate my data quality
SO THAT I catch errors before they corrupt my analytics

Acceptance Criteria
-------------------
- AC1: Runs automatically on every file upload (triggered by data_loader.py)
- AC2: Detects: missing required columns, null values, duplicate rows,
       invalid dates, negative amounts, outlier values (3σ)
- AC3: Produces a data quality SCORE (0–100)
- AC4: Shows issue summary: count, severity (error/warning/info), affected field
- AC5: Saves quality report to Agent Memory for trend tracking
- AC6: If score < 60, shows prominent warning banner on Upload tab
- AC7: Does NOT block upload — logs issues, user decides to proceed

Issue Severity
--------------
  error   : will break analytics (missing required col, all nulls)
  warning : may skew results (>10% nulls, duplicate dates, outliers)
  info    : noticed but unlikely to cause problems (extra columns)
"""

import logging
from typing import Optional

import pandas as pd
import numpy as np

from ai.agents import AgentResult

logger = logging.getLogger(__name__)

_REQUIRED_SALES_COLS  = {"bill_date", "net_amount"}
_REQUIRED_PURCH_COLS  = {"grn_date", "net_amount"}
_POSITIVE_COLS        = {"net_amount", "total_gst", "quantity", "unit_price"}
_DATE_COLS            = {"bill_date", "grn_date"}


class QualityAgent:
    """
    Validates DataFrames for completeness, consistency, and plausibility.

    Designed to run on every upload (called from data_loader.py)
    and also answerable as a conversational question.
    """

    def __init__(self, memory=None):
        self.memory = memory

    def run(
        self,
        question: str = "",
        sales_df: Optional[pd.DataFrame] = None,
        purchase_df: Optional[pd.DataFrame] = None,
        tenant_id: int = 0,
        language: str = "English",
        upload_id: Optional[int] = None,
    ) -> AgentResult:
        steps: list[dict] = []
        all_issues: list[dict] = []

        # Validate each DataFrame
        s_issues, s_score = self._validate(sales_df,    "sales",    _REQUIRED_SALES_COLS)
        p_issues, p_score = self._validate(purchase_df, "purchases", _REQUIRED_PURCH_COLS)

        all_issues = s_issues + p_issues
        combined_score = (
            (s_score + p_score) / 2
            if (sales_df is not None and purchase_df is not None)
            else (s_score if sales_df is not None else p_score)
        )

        steps.append({
            "step": 1,
            "thought": "Scanning data for quality issues",
            "action":  "validate(sales_df) + validate(purchase_df)",
            "observation": (
                f"Sales issues: {len(s_issues)} | Purchase issues: {len(p_issues)} | "
                f"Combined score: {combined_score:.0f}/100"
            ),
        })

        # Dedup check
        dup_info = self._check_duplicates(sales_df, purchase_df)
        if dup_info["found"]:
            all_issues.extend(dup_info["issues"])
            steps.append({
                "step": 2,
                "thought": "Checking for duplicate rows",
                "action":  "df.duplicated().sum()",
                "observation": dup_info["summary"],
            })

        # Outlier check
        outlier_info = self._check_outliers(sales_df)
        if outlier_info["found"]:
            all_issues.extend(outlier_info["issues"])
            steps.append({
                "step": 3,
                "thought": "Checking for statistical outliers (>3σ from mean)",
                "action":  "z_score = (val - mean) / std; flag if abs(z) > 3",
                "observation": outlier_info["summary"],
            })

        # Recalculate final score with all issues
        error_count   = sum(1 for i in all_issues if i.get("severity") == "error")
        warning_count = sum(1 for i in all_issues if i.get("severity") == "warning")
        final_score   = max(0, min(100, 100 - (error_count * 15) - (warning_count * 5)))

        steps.append({
            "step": len(steps) + 1,
            "thought": "Computing final data quality score",
            "action":  "score = 100 - (errors×15) - (warnings×5)",
            "observation": (
                f"Final score: {final_score}/100 | "
                f"Errors: {error_count} | Warnings: {warning_count} | "
                f"Grade: {'A' if final_score >= 90 else 'B' if final_score >= 75 else 'C' if final_score >= 60 else 'D'}"
            ),
        })

        # Save to memory
        if self.memory:
            total_rows = (
                (len(sales_df) if sales_df is not None else 0) +
                (len(purchase_df) if purchase_df is not None else 0)
            )
            valid_rows = max(0, total_rows - error_count * 10)
            try:
                self.memory.save_quality_report(
                    tenant_id=tenant_id,
                    total_rows=total_rows,
                    valid_rows=valid_rows,
                    issues=all_issues,
                    score=final_score,
                    upload_id=upload_id,
                )
            except Exception as exc:
                logger.warning("[QualityAgent] Failed to save quality report: %s", exc)

        answer = self._format_answer(all_issues, final_score)

        return AgentResult(
            answer=answer,
            agent="quality",
            reasoning_steps=steps,
            confidence=1.0,
            sources=["sales_df", "purchase_df", "pandas_validation"],
            metadata={
                "score":         final_score,
                "issues":        all_issues,
                "error_count":   error_count,
                "warning_count": warning_count,
            },
        )

    # -------------------------------------------------------------------------
    # Validation helpers
    # -------------------------------------------------------------------------

    def _validate(
        self,
        df: Optional[pd.DataFrame],
        label: str,
        required_cols: set[str],
    ) -> tuple[list[dict], float]:
        """Run standard validation checks. Returns (issues, score)."""
        if df is None or df.empty:
            return [], 100.0

        issues: list[dict] = []
        score = 100.0
        cols  = set(df.columns)

        # 1. Required columns
        missing_req = required_cols - cols
        if missing_req:
            issues.append({
                "field":    ", ".join(missing_req),
                "severity": "error",
                "message":  f"Required column(s) missing from {label} data",
                "count":    len(missing_req),
            })
            score -= 20

        # 2. Null values
        for col in cols:
            null_count = int(df[col].isna().sum())
            null_pct   = null_count / len(df) * 100
            if null_pct > 50:
                issues.append({
                    "field": col, "severity": "error",
                    "message": f"{null_pct:.0f}% nulls in {label}.{col}",
                    "count": null_count,
                })
                score -= 10
            elif null_pct > 10:
                issues.append({
                    "field": col, "severity": "warning",
                    "message": f"{null_pct:.0f}% nulls in {label}.{col}",
                    "count": null_count,
                })
                score -= 3

        # 3. Invalid dates
        for col in DATE_COLS if hasattr(QualityAgent, '_DATE_COLS') else _DATE_COLS:
            if col in cols:
                parsed = pd.to_datetime(df[col], errors="coerce")
                bad    = int(parsed.isna().sum()) - int(df[col].isna().sum())
                if bad > 0:
                    issues.append({
                        "field": col, "severity": "error",
                        "message": f"{bad} unparseable date values in {label}.{col}",
                        "count": bad,
                    })
                    score -= 8

        # 4. Negative amounts
        for col in _POSITIVE_COLS:
            if col in cols:
                neg = int((pd.to_numeric(df[col], errors="coerce").fillna(0) < 0).sum())
                if neg > 0:
                    issues.append({
                        "field": col, "severity": "warning",
                        "message": f"{neg} negative values in {label}.{col} (returns/refunds?)",
                        "count": neg,
                    })
                    score -= 2

        return issues, max(0.0, score)

    def _check_duplicates(
        self,
        sales_df: Optional[pd.DataFrame],
        purchase_df: Optional[pd.DataFrame],
    ) -> dict:
        """Check for exact duplicate rows."""
        results = {"found": False, "issues": [], "summary": "No duplicates found"}

        for label, df in [("sales", sales_df), ("purchases", purchase_df)]:
            if df is None or df.empty:
                continue
            dup_count = int(df.duplicated().sum())
            if dup_count > 0:
                results["found"] = True
                results["issues"].append({
                    "field": f"{label}_all",
                    "severity": "warning",
                    "message": f"{dup_count} exact duplicate rows in {label} data",
                    "count": dup_count,
                })

        if results["found"]:
            results["summary"] = " | ".join(
                f"{i['count']} duplicates in {i['field']}" for i in results["issues"]
            )
        return results

    def _check_outliers(
        self,
        sales_df: Optional[pd.DataFrame],
        z_threshold: float = 3.0,
    ) -> dict:
        """Flag values more than z_threshold standard deviations from mean."""
        results = {"found": False, "issues": [], "summary": "No outliers detected"}

        if sales_df is None or sales_df.empty or "net_amount" not in sales_df.columns:
            return results

        try:
            vals  = pd.to_numeric(sales_df["net_amount"], errors="coerce").dropna()
            if len(vals) < 10:
                return results
            mean  = vals.mean()
            std   = vals.std()
            if std == 0:
                return results
            z     = (vals - mean).abs() / std
            count = int((z > z_threshold).sum())

            if count > 0:
                results["found"] = True
                results["issues"].append({
                    "field":    "net_amount",
                    "severity": "warning",
                    "message":  (
                        f"{count} outlier transaction(s) in sales (>{z_threshold}σ from mean). "
                        f"Mean={mean:,.0f}, max outlier={vals[z > z_threshold].max():,.0f}"
                    ),
                    "count": count,
                })
                results["summary"] = f"{count} outlier(s) in net_amount (>{z_threshold}σ)"
        except Exception as exc:
            logger.warning("[QualityAgent] Outlier check error: %s", exc)

        return results

    def _format_answer(self, issues: list[dict], score: float) -> str:
        grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
        emoji = "✅" if score >= 75 else "⚠️" if score >= 50 else "❌"

        lines = [
            f"{emoji} **Data Quality Score: {score:.0f}/100 (Grade {grade})**",
            "",
        ]

        if not issues:
            lines.append("Your data looks clean — no issues detected.")
            return "\n".join(lines)

        errors   = [i for i in issues if i["severity"] == "error"]
        warnings = [i for i in issues if i["severity"] == "warning"]
        infos    = [i for i in issues if i["severity"] == "info"]

        if errors:
            lines.append(f"**{len(errors)} Error(s)** — fix before analyzing:")
            for i in errors:
                lines.append(f"  ❌ `{i['field']}`: {i['message']}")
            lines.append("")

        if warnings:
            lines.append(f"**{len(warnings)} Warning(s)** — may affect accuracy:")
            for i in warnings:
                lines.append(f"  ⚠️ `{i['field']}`: {i['message']}")
            lines.append("")

        if infos:
            lines.append(f"**{len(infos)} Info** — FYI:")
            for i in infos:
                lines.append(f"  ℹ️ `{i['field']}`: {i['message']}")

        if score < 60:
            lines.append(
                "\n🔴 **Data quality is low.** Analytics results may be unreliable. "
                "Fix the errors above before running reports."
            )

        return "\n".join(lines)


_DATE_COLS = _DATE_COLS  # re-export for _validate scope fix
