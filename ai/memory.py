"""
ai/memory.py  --  SQLite-backed Agent Memory for InsightHub Multi-Agent Platform
=================================================================================

Provides cross-session persistence for:
  - schema_mappings   : file format -> canonical column map (per tenant)
  - agent_insights    : past ReAct reasoning traces + answers (per tenant)
  - agent_sessions    : conversation context (message history + active agent)
  - user_preferences  : language, preferred KPIs, default date range (per tenant)

Phase 3 upgrade path: replace SQLite vectors with ChromaDB for semantic search
across all stored insights.

Usage
-----
from ai.memory import AgentMemory

mem = AgentMemory("/path/to/medstar.db")

# Store a schema mapping
mem.save_schema_mapping(tenant_id=1, source_format="quickbooks",
                        raw_columns=["Date","Memo","Amount"],
                        canonical_map={"Date":"bill_date","Amount":"net_amount"})

# Retrieve it next time same file is uploaded
mapping = mem.get_schema_mapping(tenant_id=1, source_format="quickbooks")

# Store an insight from Insight Agent
mem.save_insight(tenant_id=1, question="Why did margin drop in April?",
                 agent="insight", answer="Supplier X raised prices 18%",
                 reasoning_steps=[...])

# Retrieve last 5 insights for context injection
recent = mem.get_recent_insights(tenant_id=1, limit=5)
"""

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

_INIT_SQL = [
    # --- Schema Mappings -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS schema_mappings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL DEFAULT 0,
        source_format   TEXT NOT NULL,          -- 'quickbooks' | 'square' | 'shopify' | 'marg' | 'custom'
        raw_columns     TEXT NOT NULL,          -- JSON: ["Date","Memo","Amount",...]
        canonical_map   TEXT NOT NULL,          -- JSON: {"Date":"bill_date","Amount":"net_amount",...}
        domain          TEXT DEFAULT 'pharmacy',-- 'pharmacy' | 'retail' | 'restaurant'
        confidence      REAL DEFAULT 1.0,
        sample_rows     TEXT,                   -- JSON: first 3 rows for future reference
        created_at      TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(tenant_id, source_format)
    )
    """,
    # --- Agent Insights --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS agent_insights (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL DEFAULT 0,
        question        TEXT NOT NULL,
        agent           TEXT NOT NULL,          -- 'analytics' | 'insight' | 'forecast' | 'schema' | 'quality'
        answer          TEXT NOT NULL,
        reasoning_steps TEXT,                   -- JSON: [{step, thought, action, observation}]
        kpi_snapshot    TEXT,                   -- JSON: KPI values at time of insight
        data_hash       TEXT,                   -- hash of data context used (detect staleness)
        language        TEXT DEFAULT 'English',
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    # --- Agent Sessions --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS agent_sessions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL DEFAULT 0,
        session_token   TEXT NOT NULL,
        messages        TEXT NOT NULL DEFAULT '[]',  -- JSON chat history
        active_agent    TEXT,                        -- last agent that responded
        context_hash    TEXT,                        -- hash to detect stale context
        created_at      TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    # --- User Preferences ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS user_preferences (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL DEFAULT 0,
        language        TEXT DEFAULT 'English',
        preferred_kpis  TEXT DEFAULT '[]',      -- JSON: ["revenue","margin","top_branch"]
        default_days    INTEGER DEFAULT 30,
        timezone        TEXT DEFAULT 'Asia/Kolkata',
        currency        TEXT DEFAULT 'INR',
        updated_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(tenant_id)
    )
    """,
    # --- Data Quality Log -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS quality_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL DEFAULT 0,
        upload_id       INTEGER,
        source_format   TEXT,
        total_rows      INTEGER,
        valid_rows      INTEGER,
        issues          TEXT,                   -- JSON: [{field, severity, message, count}]
        score           REAL DEFAULT 100.0,     -- 0-100 data quality score
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
]


class AgentMemory:
    """
    SQLite-backed persistent memory for all InsightHub agents.

    Designed to be instantiated once at startup and shared across all agents
    via dependency injection.
    """

    def __init__(self, db_path: str):
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self._init_tables()

    def _init_tables(self):
        with self.engine.connect() as conn:
            for sql in _INIT_SQL:
                conn.execute(text(sql))
            conn.commit()
        logger.info("[AgentMemory] Tables ready")

    # =========================================================================
    # Schema Mappings
    # =========================================================================

    def save_schema_mapping(
        self,
        tenant_id: int,
        source_format: str,
        raw_columns: list[str],
        canonical_map: dict,
        domain: str = "pharmacy",
        confidence: float = 1.0,
        sample_rows: Optional[list] = None,
    ) -> None:
        """Upsert a schema mapping for a given tenant + format."""
        with self.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO schema_mappings
                    (tenant_id, source_format, raw_columns, canonical_map,
                     domain, confidence, sample_rows, updated_at)
                VALUES (:tid, :fmt, :cols, :cmap, :dom, :conf, :srows, :now)
                ON CONFLICT(tenant_id, source_format) DO UPDATE SET
                    raw_columns   = excluded.raw_columns,
                    canonical_map = excluded.canonical_map,
                    domain        = excluded.domain,
                    confidence    = excluded.confidence,
                    sample_rows   = excluded.sample_rows,
                    updated_at    = excluded.updated_at
            """), {
                "tid":   tenant_id,
                "fmt":   source_format.lower(),
                "cols":  json.dumps(raw_columns),
                "cmap":  json.dumps(canonical_map),
                "dom":   domain,
                "conf":  confidence,
                "srows": json.dumps(sample_rows or []),
                "now":   datetime.utcnow().isoformat(),
            })
            conn.commit()
        logger.info("[AgentMemory] Saved schema mapping: tenant=%s format=%s", tenant_id, source_format)

    def get_schema_mapping(
        self,
        tenant_id: int,
        source_format: str,
    ) -> Optional[dict]:
        """Return saved schema mapping or None."""
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT raw_columns, canonical_map, domain, confidence, updated_at
                FROM schema_mappings
                WHERE tenant_id=:tid AND source_format=:fmt
            """), {"tid": tenant_id, "fmt": source_format.lower()}).fetchone()
        if not row:
            return None
        return {
            "raw_columns":   json.loads(row[0]),
            "canonical_map": json.loads(row[1]),
            "domain":        row[2],
            "confidence":    row[3],
            "updated_at":    row[4],
        }

    def list_schema_mappings(self, tenant_id: int) -> list[dict]:
        """Return all saved schema mappings for a tenant."""
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT source_format, domain, confidence, updated_at
                FROM schema_mappings WHERE tenant_id=:tid ORDER BY updated_at DESC
            """), {"tid": tenant_id}).fetchall()
        return [{"format": r[0], "domain": r[1], "confidence": r[2], "updated_at": r[3]} for r in rows]

    # =========================================================================
    # Agent Insights
    # =========================================================================

    def save_insight(
        self,
        tenant_id: int,
        question: str,
        agent: str,
        answer: str,
        reasoning_steps: Optional[list] = None,
        kpi_snapshot: Optional[dict] = None,
        language: str = "English",
        data_hash: str = "",
    ) -> int:
        """Persist an agent insight. Returns the new insight ID."""
        with self.engine.connect() as conn:
            result = conn.execute(text("""
                INSERT INTO agent_insights
                    (tenant_id, question, agent, answer, reasoning_steps,
                     kpi_snapshot, data_hash, language)
                VALUES (:tid, :q, :agent, :ans, :steps, :kpi, :dhash, :lang)
            """), {
                "tid":   tenant_id,
                "q":     question,
                "agent": agent,
                "ans":   answer,
                "steps": json.dumps(reasoning_steps or []),
                "kpi":   json.dumps(kpi_snapshot or {}),
                "dhash": data_hash,
                "lang":  language,
            })
            conn.commit()
            return result.lastrowid

    def get_recent_insights(
        self,
        tenant_id: int,
        limit: int = 5,
        agent: Optional[str] = None,
    ) -> list[dict]:
        """Return N most recent insights for context injection."""
        q = """
            SELECT id, question, agent, answer, reasoning_steps, created_at
            FROM agent_insights WHERE tenant_id=:tid
        """
        params: dict = {"tid": tenant_id}
        if agent:
            q += " AND agent=:agent"
            params["agent"] = agent
        q += " ORDER BY created_at DESC LIMIT :lim"
        params["lim"] = limit

        with self.engine.connect() as conn:
            rows = conn.execute(text(q), params).fetchall()

        return [
            {
                "id":         r[0],
                "question":   r[1],
                "agent":      r[2],
                "answer":     r[3],
                "steps":      json.loads(r[4] or "[]"),
                "created_at": r[5],
            }
            for r in rows
        ]

    def search_insights(self, tenant_id: int, keyword: str, limit: int = 3) -> list[dict]:
        """Simple keyword search over past insights (Phase 3: replace with ChromaDB)."""
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, question, agent, answer, created_at
                FROM agent_insights
                WHERE tenant_id=:tid
                  AND (question LIKE :kw OR answer LIKE :kw)
                ORDER BY created_at DESC
                LIMIT :lim
            """), {"tid": tenant_id, "kw": f"%{keyword}%", "lim": limit}).fetchall()
        return [{"id": r[0], "question": r[1], "agent": r[2], "answer": r[3], "created_at": r[4]} for r in rows]

    # =========================================================================
    # User Preferences
    # =========================================================================

    def get_preferences(self, tenant_id: int) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT language, preferred_kpis, default_days, timezone, currency
                FROM user_preferences WHERE tenant_id=:tid
            """), {"tid": tenant_id}).fetchone()
        if not row:
            return {"language": "English", "preferred_kpis": [], "default_days": 30,
                    "timezone": "Asia/Kolkata", "currency": "INR"}
        return {
            "language":       row[0],
            "preferred_kpis": json.loads(row[1] or "[]"),
            "default_days":   row[2],
            "timezone":       row[3],
            "currency":       row[4],
        }

    def save_preferences(self, tenant_id: int, **kwargs) -> None:
        prefs = self.get_preferences(tenant_id)
        prefs.update(kwargs)
        with self.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO user_preferences
                    (tenant_id, language, preferred_kpis, default_days, timezone, currency, updated_at)
                VALUES (:tid, :lang, :kpis, :days, :tz, :cur, :now)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    language      = excluded.language,
                    preferred_kpis= excluded.preferred_kpis,
                    default_days  = excluded.default_days,
                    timezone      = excluded.timezone,
                    currency      = excluded.currency,
                    updated_at    = excluded.updated_at
            """), {
                "tid":  tenant_id,
                "lang": prefs["language"],
                "kpis": json.dumps(prefs["preferred_kpis"]),
                "days": prefs["default_days"],
                "tz":   prefs["timezone"],
                "cur":  prefs["currency"],
                "now":  datetime.utcnow().isoformat(),
            })
            conn.commit()

    # =========================================================================
    # Data Quality Log
    # =========================================================================

    def save_quality_report(
        self,
        tenant_id: int,
        total_rows: int,
        valid_rows: int,
        issues: list[dict],
        score: float,
        upload_id: Optional[int] = None,
        source_format: str = "unknown",
    ) -> int:
        with self.engine.connect() as conn:
            result = conn.execute(text("""
                INSERT INTO quality_log
                    (tenant_id, upload_id, source_format, total_rows, valid_rows, issues, score)
                VALUES (:tid, :uid, :fmt, :tot, :val, :iss, :sc)
            """), {
                "tid": tenant_id, "uid": upload_id, "fmt": source_format,
                "tot": total_rows, "val": valid_rows,
                "iss": json.dumps(issues), "sc": score,
            })
            conn.commit()
            return result.lastrowid

    def get_quality_history(self, tenant_id: int, limit: int = 10) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT upload_id, source_format, total_rows, valid_rows, score, created_at
                FROM quality_log WHERE tenant_id=:tid ORDER BY created_at DESC LIMIT :lim
            """), {"tid": tenant_id, "lim": limit}).fetchall()
        return [
            {"upload_id": r[0], "format": r[1], "total": r[2],
             "valid": r[3], "score": r[4], "created_at": r[5]}
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Module-level singleton — lazy init via init_agent_memory()
# ---------------------------------------------------------------------------
_memory: Optional[AgentMemory] = None


def init_agent_memory(db_path: str) -> AgentMemory:
    """Call once at startup. Returns the singleton AgentMemory instance."""
    global _memory
    _memory = AgentMemory(db_path)
    return _memory


def get_memory() -> Optional[AgentMemory]:
    """Return the module-level AgentMemory singleton (may be None before init)."""
    return _memory
