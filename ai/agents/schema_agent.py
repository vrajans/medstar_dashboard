"""
ai/agents/schema_agent.py  --  Schema Agent (Auto-Detect + Column Mapping)
============================================================================

User Story (Phase 1 — Core demo feature)
-----------------------------------------
AS A USA SMB owner
I WANT to drop any CSV/Excel file from QuickBooks, Square, or Shopify
SO THAT InsightHub automatically understands my data without manual setup

Acceptance Criteria
-------------------
- AC1: Detects QuickBooks, Square, Shopify, Marg ERP, and generic CSV formats
- AC2: Maps raw columns to InsightHub canonical schema in < 3 seconds
- AC3: Saves mapping to Agent Memory — next upload of same format is instant
- AC4: Shows mapping result to user with confidence per column
- AC5: If confidence < 70% on a column, asks the user to confirm the mapping
- AC6: After mapping, automatically passes data to Analytics Agent for Q&A
- AC7: Works with 0 prior configuration — zero-setup onboarding

Canonical Schema (target)
--------------------------
Sales:     bill_date, net_amount, margin_pct, branch, total_bills,
           cash_sales, credit_sales, customer_name, product_name
Purchases: grn_date, supplier_name, net_amount, total_gst, product_name,
           quantity, unit_price

Source Formats
--------------
quickbooks : "Date", "Description", "Amount", "Account", "Transaction Type"
square     : "Date", "Time", "Description", "Amount", "Payment Method"
shopify    : "Name", "Created at", "Total", "Subtotal", "Billing Name"
marg       : "BILL_DATE", "PARTY_NAME", "NET_AMT", "GST_AMT", "BRANCH"
custom     : any unrecognized format — Groq maps it best-effort
"""

import json
import logging
import os
import re
from typing import Optional

import pandas as pd

from ai.agents import AgentResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known format signatures: (format_name, required_cols_subset)
# Higher specificity columns go first for reliable detection
# ---------------------------------------------------------------------------

_FORMAT_SIGNATURES: list[tuple[str, list[str], str]] = [
    # (format_id, signature_columns, display_name)
    ("marg",       ["BILL_DATE", "PARTY_NAME", "NET_AMT", "GST_AMT"],    "Marg ERP"),
    ("quickbooks", ["Transaction Type", "Account", "Memo/Description"],   "QuickBooks"),
    ("square",     ["Payment Method", "Card Brand", "PAN Suffix"],        "Square POS"),
    ("shopify",    ["Billing Name", "Subtotal", "Lineitem name"],          "Shopify"),
    ("shopify",    ["Created at", "Financial Status", "Fulfillment Status"], "Shopify"),
    ("quickbooks", ["Date", "Description", "Amount", "Transaction Type"], "QuickBooks"),
    ("square",     ["Date", "Time", "Description", "Amount"],             "Square POS"),
]

# ---------------------------------------------------------------------------
# Column mapping rules: {source_col_lower: canonical_col}
# Matched by lowercase startswith or exact match
# ---------------------------------------------------------------------------

_COLUMN_MAP_RULES: dict[str, dict[str, str]] = {
    "quickbooks": {
        "date":                 "bill_date",
        "amount":               "net_amount",
        "memo":                 "product_name",
        "memo/description":     "product_name",
        "description":          "product_name",
        "name":                 "customer_name",
        "account":              "branch",
        "transaction type":     "_tx_type",      # filtered post-map
    },
    "square": {
        "date":                 "bill_date",
        "amount":               "net_amount",
        "description":          "product_name",
        "customer name":        "customer_name",
        "location":             "branch",
        "payment method":       "cash_sales",    # 'Cash' → cash, else credit
        "net total":            "net_amount",
        "gross sales":          "net_amount",
    },
    "shopify": {
        "created at":           "bill_date",
        "total":                "net_amount",
        "subtotal":             "net_amount",
        "billing name":         "customer_name",
        "shipping city":        "branch",
        "lineitem name":        "product_name",
        "name":                 "customer_name",
        "financial status":     "_status",
    },
    "marg": {
        "bill_date":            "bill_date",
        "net_amt":              "net_amount",
        "gst_amt":              "total_gst",
        "party_name":           "supplier_name",
        "branch":               "branch",
        "grn_date":             "grn_date",
        "product_name":         "product_name",
        "qty":                  "quantity",
    },
}

_GROQ_SCHEMA_PROMPT = """You are a data schema mapping expert.

I have a CSV/Excel file with these columns: {columns}

Sample data (first 3 rows):
{sample}

The TARGET canonical schema has these fields:
Sales:     bill_date, net_amount, margin_pct, branch, total_bills, cash_sales, credit_sales, customer_name, product_name
Purchases: grn_date, supplier_name, net_amount, total_gst, product_name, quantity, unit_price

Task:
1. Identify the data source format (quickbooks/square/shopify/marg/custom)
2. Map each source column to the best canonical field. Use null for unmappable columns.
3. Determine if this is "sales" or "purchases" data

Respond ONLY with valid JSON:
{{
  "source_format": "<format>",
  "data_type": "sales|purchases",
  "mapping": {{"source_col": "canonical_col_or_null", ...}},
  "confidence": <0.0-1.0>,
  "notes": "<any important observations>"
}}
"""


class SchemaAgent:
    """
    Auto-detects file format and maps columns to InsightHub canonical schema.

    Flow:
      1. Detect format via signature matching (instant, no LLM)
      2. Apply rule-based column mapping
      3. If confidence < 0.8: confirm with Groq
      4. Save mapping to AgentMemory
      5. Return mapped DataFrame + AgentResult with trace
    """

    def __init__(self, memory=None):
        self.memory = memory

    def run(
        self,
        question: str = "",
        tenant_id: int = 0,
        language: str = "English",
        uploaded_file_info: Optional[dict] = None,
        sales_df: Optional[pd.DataFrame] = None,
        purchase_df: Optional[pd.DataFrame] = None,
    ) -> AgentResult:
        """
        Process an uploaded file or answer a schema-related question.

        uploaded_file_info: {
            "filename": str,
            "columns":  list[str],
            "df":       pd.DataFrame,
            "raw_bytes": bytes (optional)
        }
        """
        steps: list[dict] = []

        # ── Case 1: File uploaded ─────────────────────────────────────────────
        if uploaded_file_info and uploaded_file_info.get("columns"):
            return self._process_file(
                uploaded_file_info=uploaded_file_info,
                tenant_id=tenant_id,
                language=language,
                steps=steps,
            )

        # ── Case 2: Schema question (no file) ────────────────────────────────
        saved_mappings = []
        if self.memory:
            saved_mappings = self.memory.list_schema_mappings(tenant_id)

        if saved_mappings:
            fmt_list = ", ".join(f"{m['format']} ({m['domain']})" for m in saved_mappings)
            answer = (
                f"I have {len(saved_mappings)} saved schema mapping(s) for your account:\n\n"
                f"{fmt_list}\n\n"
                "Next time you upload a file in any of these formats, I'll map the columns instantly "
                "without any manual configuration. You can also upload a new file type — "
                "I'll auto-detect and learn the new format automatically."
            )
        else:
            answer = (
                "No schema mappings saved yet. Upload your first data file (CSV or Excel) "
                "and I'll automatically detect whether it's from QuickBooks, Square, Shopify, "
                "Marg ERP, or a custom format — then map it to InsightHub's analytics engine."
            )

        return AgentResult(
            answer=answer,
            agent="schema",
            reasoning_steps=steps,
            confidence=1.0,
            sources=["agent_memory"],
        )

    def _process_file(
        self,
        uploaded_file_info: dict,
        tenant_id: int,
        language: str,
        steps: list[dict],
    ) -> AgentResult:
        filename = uploaded_file_info.get("filename", "file.csv")
        columns  = uploaded_file_info.get("columns", [])
        df       = uploaded_file_info.get("df")

        # Step 1: Check memory for a known mapping
        steps.append({
            "step": 1,
            "thought": f"File uploaded: {filename} with {len(columns)} columns",
            "action":  "Checking Agent Memory for known schema mappings",
            "observation": f"Columns: {', '.join(columns[:8])}{'...' if len(columns) > 8 else ''}",
        })

        # Step 2: Detect format
        detected_format, display_name, sig_confidence = self._detect_format(columns)

        steps.append({
            "step": 2,
            "thought": f"Comparing column signatures against known formats",
            "action":  f"Signature matching → '{detected_format}' ({display_name})",
            "observation": f"Confidence: {sig_confidence:.0%}",
        })

        # Check memory first
        if self.memory:
            cached = self.memory.get_schema_mapping(tenant_id, detected_format)
            if cached and cached["confidence"] >= 0.8:
                steps.append({
                    "step": 3,
                    "thought": "Found high-confidence mapping in Agent Memory",
                    "action":  "Loading cached schema mapping",
                    "observation": f"Saved mapping from {cached['updated_at'][:10]} (conf={cached['confidence']:.0%})",
                })
                canonical_map = cached["canonical_map"]
                confidence    = cached["confidence"]
                answer        = self._format_mapping_answer(
                    filename, display_name, canonical_map, confidence, from_cache=True
                )
                mapped_df = self._apply_mapping(df, canonical_map) if df is not None else None
                return AgentResult(
                    answer=answer,
                    agent="schema",
                    reasoning_steps=steps,
                    confidence=confidence,
                    sources=["agent_memory", "uploaded_file"],
                    metadata={"canonical_map": canonical_map, "mapped_df": mapped_df,
                              "source_format": detected_format, "display_name": display_name},
                )

        # Step 3: Rule-based mapping
        canonical_map, rule_confidence = self._rule_map(columns, detected_format)

        steps.append({
            "step": 3,
            "thought": f"Applying rule-based column mapping for {display_name}",
            "action":  "Rule-based mapping",
            "observation": (
                f"Mapped {len([v for v in canonical_map.values() if v])} / {len(columns)} columns. "
                f"Confidence: {rule_confidence:.0%}"
            ),
        })

        # Step 4: If low confidence — confirm with Groq
        if rule_confidence < 0.75:
            groq_map, groq_format, groq_confidence, groq_notes = self._groq_map(
                columns, df, detected_format
            )
            if groq_confidence > rule_confidence:
                canonical_map = groq_map
                detected_format = groq_format
                rule_confidence = groq_confidence
                steps.append({
                    "step": 4,
                    "thought": f"Rule confidence was {rule_confidence:.0%} — confirming with Groq AI",
                    "action":  "Groq schema mapping",
                    "observation": f"Groq result: format={groq_format}, conf={groq_confidence:.0%}. {groq_notes}",
                })

        # Step 5: Detect domain + Save to memory
        try:
            from domain_config import get_domain_from_format, detect_domain_from_columns
            domain_type = get_domain_from_format(detected_format)
            if domain_type == "generic":
                domain_type = detect_domain_from_columns(columns)
        except Exception:
            domain_type = "generic"

        if self.memory:
            sample_rows = df.head(3).to_dict(orient="records") if df is not None else []
            self.memory.save_schema_mapping(
                tenant_id=tenant_id,
                source_format=detected_format,
                raw_columns=columns,
                canonical_map=canonical_map,
                confidence=rule_confidence,
                sample_rows=sample_rows,
            )
            # Save domain to tenant preferences so app.py can adapt the UI
            try:
                self.memory.save_preferences(
                    tenant_id=tenant_id,
                    preferences={"detected_domain": domain_type, "detected_format": detected_format},
                )
            except Exception:
                pass
            steps.append({
                "step": 5,
                "thought": f"Schema mapping learned → domain detected as '{domain_type}'",
                "action":  "Saved schema + domain to Agent Memory",
                "observation": (
                    f"Domain: {domain_type.upper()} | "
                    f"Next upload of {display_name} format will be instant. "
                    "Dashboard will adapt to your business type."
                ),
            })

        # Apply mapping to DataFrame
        mapped_df = self._apply_mapping(df, canonical_map) if df is not None else None

        answer = self._format_mapping_answer(
            filename, display_name, canonical_map, rule_confidence, from_cache=False
        )

        return AgentResult(
            answer=answer,
            agent="schema",
            reasoning_steps=steps,
            confidence=rule_confidence,
            sources=["file_signature", "rule_engine"] + (["groq"] if rule_confidence < 0.75 else []),
            metadata={
                "canonical_map":  canonical_map,
                "mapped_df":      mapped_df,
                "source_format":  detected_format,
                "display_name":   display_name,
                "domain_type":    domain_type,
                "total_rows":     len(df) if df is not None else 0,
                "mapped_columns": {k: v for k, v in canonical_map.items() if v},
            },
        )

    def _detect_format(self, columns: list[str]) -> tuple[str, str, float]:
        """Return (format_id, display_name, confidence) based on column signatures."""
        cols_lower = {c.lower().strip() for c in columns}
        cols_set   = {c.strip() for c in columns}

        best_format, best_name, best_score = "custom", "Custom CSV", 0.0

        for fmt, sig_cols, display in _FORMAT_SIGNATURES:
            matches = sum(
                1 for c in sig_cols
                if c.strip() in cols_set or c.lower().strip() in cols_lower
            )
            score = matches / len(sig_cols)
            if score > best_score:
                best_score  = score
                best_format = fmt
                best_name   = display

        if best_score < 0.4:
            return "custom", "Custom CSV", 0.4
        return best_format, best_name, min(0.95, best_score + 0.1)

    def _rule_map(
        self, columns: list[str], source_format: str
    ) -> tuple[dict, float]:
        """Map source columns to canonical using rule dictionaries."""
        rules   = _COLUMN_MAP_RULES.get(source_format, {})
        mapping: dict[str, Optional[str]] = {}
        mapped  = 0

        for col in columns:
            col_lower = col.lower().strip()
            canonical = rules.get(col_lower)
            if not canonical:
                # Partial match
                for rule_key, rule_val in rules.items():
                    if col_lower.startswith(rule_key) or rule_key in col_lower:
                        canonical = rule_val
                        break
            # Generic fallbacks
            if not canonical:
                if "date" in col_lower:        canonical = "bill_date"
                elif "amount" in col_lower or "total" in col_lower: canonical = "net_amount"
                elif "supplier" in col_lower or "vendor" in col_lower: canonical = "supplier_name"
                elif "customer" in col_lower:  canonical = "customer_name"
                elif "branch" in col_lower or "location" in col_lower: canonical = "branch"
                elif "gst" in col_lower or "tax" in col_lower: canonical = "total_gst"
                elif "qty" in col_lower or "quant" in col_lower: canonical = "quantity"
                elif "product" in col_lower or "item" in col_lower or "desc" in col_lower:
                    canonical = "product_name"

            mapping[col] = canonical
            if canonical and not canonical.startswith("_"):
                mapped += 1

        confidence = mapped / max(len(columns), 1)
        return mapping, confidence

    def _groq_map(
        self,
        columns: list[str],
        df: Optional[pd.DataFrame],
        fallback_format: str,
    ) -> tuple[dict, str, float, str]:
        """Ask Groq to map columns when rule engine isn't confident."""
        try:
            from groq import Groq as _GroqClient

            api_key = os.environ.get("GROQ_API_KEY", "")
            if not api_key or df is None:
                return {}, fallback_format, 0.0, "No API key or data"

            sample = df.head(3).to_string(max_cols=15, max_colwidth=20)
            prompt = _GROQ_SCHEMA_PROMPT.format(
                columns=", ".join(columns),
                sample=sample[:800],
            )

            client   = _GroqClient(api_key=api_key)
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.0,
            )
            raw = response.choices[0].message.content.strip()
            m   = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                parsed = json.loads(m.group())
                return (
                    parsed.get("mapping", {}),
                    parsed.get("source_format", fallback_format),
                    float(parsed.get("confidence", 0.7)),
                    parsed.get("notes", ""),
                )
        except Exception as exc:
            logger.warning("[SchemaAgent] Groq mapping error: %s", exc)

        return {}, fallback_format, 0.0, "Groq mapping failed"

    def _apply_mapping(
        self, df: pd.DataFrame, canonical_map: dict
    ) -> pd.DataFrame:
        """Rename DataFrame columns to canonical names, drop unmapped."""
        rename  = {k: v for k, v in canonical_map.items() if v and not v.startswith("_")}
        keep    = list(rename.keys())
        mapped  = df[keep].rename(columns=rename) if keep else df.copy()
        return mapped

    def _format_mapping_answer(
        self,
        filename: str,
        display_name: str,
        canonical_map: dict,
        confidence: float,
        from_cache: bool,
    ) -> str:
        mapped_cols = {k: v for k, v in canonical_map.items() if v and not v.startswith("_")}
        unmapped    = [k for k, v in canonical_map.items() if not v]

        cache_note = " (loaded from Agent Memory — instant mapping!)" if from_cache else ""

        lines = [
            f"**Schema Agent detected: {display_name}**{cache_note}",
            f"File: `{filename}` | Confidence: {confidence:.0%}",
            "",
            "**Column Mapping:**",
        ]
        for src, dst in mapped_cols.items():
            lines.append(f"  `{src}` → `{dst}`")

        if unmapped:
            lines.append(f"\n  _Unmapped columns (not needed): {', '.join(unmapped[:5])}_")

        lines += [
            "",
            "✅ Data is ready for analysis. You can now ask questions like:",
            '  "What was my total revenue last month?"',
            '  "Which branch had the highest margin?"',
            '  "Show me the top 5 customers by spend"',
        ]
        return "\n".join(lines)
