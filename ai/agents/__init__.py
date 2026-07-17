"""
ai/agents/ — InsightHub Multi-Agent Platform
============================================

Agents:
  router          -- intent classifier + dispatcher (Groq)
  schema_agent    -- auto-detect file format + column mapping
  analytics_agent -- direct data injection Q&A
  insight_agent   -- multi-step ReAct reasoning loop
  forecast_agent  -- trend extrapolation + 30/90-day projections
  quality_agent   -- upload validation + anomaly flagging

All agents share:
  - ai.memory.AgentMemory  (SQLite persistent memory)
  - ai.groq_client.chat    (Groq Llama 3.1 70B)
  - Common AgentResult dataclass

Phase 3 upgrade: ChromaDB replaces SQLite keyword search for semantic
similarity across all historical insights.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentResult:
    """
    Unified response object returned by every agent.

    Fields
    ------
    answer          Final answer text shown to the user
    agent           Which agent produced this result
    reasoning_steps List of dicts with keys: step, thought, action, observation
    confidence      0.0-1.0 (agent self-assessment)
    sources         Data sources used (e.g. ["sales_df", "schema_mapping"])
    error           Non-empty if something went wrong
    metadata        Agent-specific extras (schema map, forecast table, etc.)
    """
    answer:          str
    agent:           str
    reasoning_steps: list[dict]       = field(default_factory=list)
    confidence:      float            = 1.0
    sources:         list[str]        = field(default_factory=list)
    error:           str              = ""
    metadata:        dict             = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error

    def to_dict(self) -> dict:
        return {
            "answer":          self.answer,
            "agent":           self.agent,
            "reasoning_steps": self.reasoning_steps,
            "confidence":      self.confidence,
            "sources":         self.sources,
            "error":           self.error,
            "metadata":        self.metadata,
        }
