"""
ai/agents/analytics_agent.py  --  Analytics Agent (Direct Data Injection Q&A)
================================================================================

User Story (Phase 1)
---------------------
AS A business owner
I WANT to ask plain-English questions about my sales, branch, and supplier data
SO THAT I get accurate, number-backed answers instantly without writing SQL

Acceptance Criteria
-------------------
- AC1: Answers metric questions in < 3 seconds using Groq 128k context window
- AC2: Injects last 60 days daily sales + branch breakdown + top suppliers
- AC3: Includes KPI snapshot (total revenue, margin, top branch) in every answer
- AC4: Maintains conversation history (multi-turn Q&A)
- AC5: Answers in the user's chosen language (English/Tamil/Hindi)
- AC6: Shows data sources used in reasoning trace
- AC7: Saves each answer to Agent Memory for future context

This is the default agent for most business questions. Unlike RAG/vector search,
it injects actual data tables into Groq's 128k context — giving exact numbers,
not semantic approximations.
"""

import logging
from typing import Optional

import pandas as pd

from ai.agents import AgentResult

logger = logging.getLogger(__name__)


class AnalyticsAgent:
    """
    Direct data injection Q&A agent.

    Builds a compact text representation of the business data and injects it
    into Groq's system prompt for highly accurate, number-grounded answers.
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

        # Step 1: Build data context
        from ai.rag import build_data_context

        steps.append({
            "step": 1,
            "thought": "Preparing business data context for injection into AI prompt",
            "action":  "build_data_context(sales_df, purchase_df)",
            "observation": (
                f"Sales rows: {len(sales_df) if sales_df is not None else 0} | "
                f"Purchase rows: {len(purchase_df) if purchase_df is not None else 0}"
            ),
        })

        data_context = build_data_context(sales_df, purchase_df)

        # Step 2: Check memory for relevant past insights
        prior_context = ""
        if self.memory:
            related = self.memory.search_insights(tenant_id, keyword=question[:40], limit=2)
            if related:
                prior_context = "\n\nRELATED PAST INSIGHTS:\n" + "\n".join(
                    f"Q: {r['question']}\nA: {r['answer'][:200]}" for r in related
                )
                steps.append({
                    "step": 2,
                    "thought": "Checking Agent Memory for related past answers",
                    "action":  "search_insights(keyword)",
                    "observation": f"Found {len(related)} related insight(s) — injecting as context",
                })

        # Step 3: Build KPI context
        from ai.groq_client import build_kpi_context

        kpi_context  = build_kpi_context(kpi_data or {}, rag_results=[])
        full_context = f"{kpi_context}\n\n{data_context}{prior_context}"

        steps.append({
            "step": 3 if prior_context else 2,
            "thought": f"Context ready: {len(full_context):,} chars. Calling Groq Llama 3.1",
            "action":  "groq.chat(system=full_context, user=question)",
            "observation": "Sending to Groq for answer generation...",
        })

        # Step 4: Call Groq
        try:
            from ai.groq_client import chat

            answer, updated_history = chat(
                user_message=question,
                history=history or [],
                context=full_context,
                language=language,
            )
        except Exception as exc:
            logger.error("[AnalyticsAgent] Groq error: %s", exc)
            answer = f"I couldn't connect to the AI service: {exc}. Please check your GROQ_API_KEY."
            updated_history = history or []

        steps.append({
            "step": len(steps) + 1,
            "thought": "Groq returned an answer",
            "action":  "Return answer to user",
            "observation": f"Answer length: {len(answer)} chars",
        })

        # Step 5: Save to memory
        if self.memory and answer and "couldn't connect" not in answer:
            try:
                self.memory.save_insight(
                    tenant_id=tenant_id,
                    question=question,
                    agent="analytics",
                    answer=answer,
                    reasoning_steps=steps,
                    kpi_snapshot=kpi_data or {},
                    language=language,
                )
            except Exception as exc:
                logger.warning("[AnalyticsAgent] Failed to save insight: %s", exc)

        return AgentResult(
            answer=answer,
            agent="analytics",
            reasoning_steps=steps,
            confidence=0.9,
            sources=["sales_df", "purchase_df", "groq_llama3.1"],
            metadata={"updated_history": updated_history},
        )
