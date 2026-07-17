"""
domain_config.py  —  InsightHub Domain-Adaptive Configuration
==============================================================

Defines the UI, terminology, and data mapping for each supported business domain.
When a tenant uploads data, the Schema Agent detects the domain and saves it to
Agent Memory. The dashboard then reads this config to render the appropriate:
  • Tab labels and navigation
  • KPI card titles and units
  • Chart axis labels and colour schemes
  • Sidebar terminology

Supported domains
-----------------
  pharmacy    — India pharmacy / medical distributor (default, current)
  saas        — Software / SaaS company (MRR, ARR, Churn, Customers)
  retail      — Retail / E-commerce (Orders, AOV, Inventory, Returns)
  accounting  — General accounting / finance (P&L, Cash Flow, Expenses)
  generic     — Any other business (revenue / cost / margin)

Usage
-----
    from domain_config import get_domain_config, detect_domain_from_columns

    cfg = get_domain_config("saas")
    print(cfg["kpi_labels"]["revenue"])   # → "Monthly Revenue"
    print(cfg["tabs"])                    # → ["overview", "revenue", ...]
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Master domain definitions
# ---------------------------------------------------------------------------

DOMAIN_CONFIGS: dict[str, dict] = {

    # ── PHARMACY / MEDICAL ──────────────────────────────────────────────────
    "pharmacy": {
        "label":       "Pharmacy / Medical",
        "icon":        "💊",
        "color":       "#1e7e4b",          # InsightHub green

        "kpi_labels": {
            "revenue":      "Total Sales",
            "cost":         "Total Purchases",
            "margin":       "Avg Margin",
            "transactions": "Bills",
            "top_entity":   "Top Branch",
            "secondary":    "Expiry Alerts",
        },

        "kpi_units": {
            "revenue":  "₹",
            "cost":     "₹",
            "margin":   "%",
        },

        "tabs": [
            {"id": "overview",   "label": "Overview",        "icon": "📊"},
            {"id": "sales",      "label": "Sales",           "icon": "💰"},
            {"id": "purchases",  "label": "Purchases",       "icon": "🛒"},
            {"id": "compare",    "label": "Branch Compare",  "icon": "🏪"},
            {"id": "yoy",        "label": "Year on Year",    "icon": "📅"},
            {"id": "gst",        "label": "GST / Tax",       "icon": "🧾"},
            {"id": "stock",      "label": "Stock & Expiry",  "icon": "📦"},
            {"id": "ai_chat",    "label": "AI Insights",     "icon": "🤖"},
            {"id": "upload",     "label": "Upload Data",     "icon": "📤"},
        ],

        # canonical column names for this domain
        "date_col":   "bill_date",
        "amount_col": "net_amount",
        "cost_col":   "net_amount",      # purchase side
        "group_col":  "branch_name",
        "item_col":   "drug_name",

        # column signatures used to auto-detect this domain
        "column_signatures": [
            "bill_date", "drug_name", "branch_name", "grn_date",
            "batch_no", "expiry_date", "hsn_code", "gst_amount",
        ],

        # suggested AI questions for this domain
        "suggested_questions": [
            "📊 How did we perform last month?",
            "🏪 Which branch had the highest margin?",
            "📈 Show me the monthly sales trend",
            "🏭 Who are our top suppliers by purchase value?",
            "💳 What is our cash vs credit sales ratio?",
            "⚠️ Are there any anomalies in our sales data?",
        ],
    },

    # ── SAAS / SOFTWARE ─────────────────────────────────────────────────────
    "saas": {
        "label":       "SaaS / Software",
        "icon":        "💻",
        "color":       "#0d6efd",          # blue

        "kpi_labels": {
            "revenue":      "Monthly Revenue",
            "cost":         "Operating Costs",
            "margin":       "Gross Margin",
            "transactions": "Active Customers",
            "top_entity":   "Top Product",
            "secondary":    "Churn Rate",
        },

        "kpi_units": {
            "revenue":  "$",
            "cost":     "$",
            "margin":   "%",
        },

        "tabs": [
            {"id": "overview",   "label": "Overview",        "icon": "📊"},
            {"id": "revenue",    "label": "Revenue",         "icon": "💰"},
            {"id": "costs",      "label": "Costs",           "icon": "💸"},
            {"id": "customers",  "label": "Customers",       "icon": "👥"},
            {"id": "yoy",        "label": "Year on Year",    "icon": "📅"},
            {"id": "ai_chat",    "label": "AI Insights",     "icon": "🤖"},
            {"id": "upload",     "label": "Upload Data",     "icon": "📤"},
        ],

        "date_col":   "date",
        "amount_col": "revenue",
        "cost_col":   "cost",
        "group_col":  "product",
        "item_col":   "product",

        "column_signatures": [
            "mrr", "arr", "churn", "churn_rate", "customer_count",
            "new_customers", "churned_customers", "ltv", "cac",
            "subscription", "plan", "seats",
        ],

        "suggested_questions": [
            "💰 What is our MRR this month?",
            "📈 Show the revenue growth trend",
            "👥 How many customers do we have?",
            "📉 What is our churn rate?",
            "🔮 Predict revenue for next quarter",
            "💸 What are our biggest cost drivers?",
        ],
    },

    # ── RETAIL / E-COMMERCE ─────────────────────────────────────────────────
    "retail": {
        "label":       "Retail / E-commerce",
        "icon":        "🛍️",
        "color":       "#fd7e14",          # orange

        "kpi_labels": {
            "revenue":      "Total Revenue",
            "cost":         "Cost of Goods",
            "margin":       "Gross Margin",
            "transactions": "Total Orders",
            "top_entity":   "Top Product",
            "secondary":    "Avg Order Value",
        },

        "kpi_units": {
            "revenue":  "$",
            "cost":     "$",
            "margin":   "%",
        },

        "tabs": [
            {"id": "overview",   "label": "Overview",        "icon": "📊"},
            {"id": "revenue",    "label": "Revenue",         "icon": "💰"},
            {"id": "inventory",  "label": "Inventory",       "icon": "📦"},
            {"id": "customers",  "label": "Customers",       "icon": "👥"},
            {"id": "yoy",        "label": "Year on Year",    "icon": "📅"},
            {"id": "ai_chat",    "label": "AI Insights",     "icon": "🤖"},
            {"id": "upload",     "label": "Upload Data",     "icon": "📤"},
        ],

        "date_col":   "date",
        "amount_col": "amount",
        "cost_col":   "cost",
        "group_col":  "product_name",
        "item_col":   "product_name",

        "column_signatures": [
            "order_id", "product_name", "sku", "quantity", "unit_price",
            "discount", "shipping", "returns", "aov", "category",
        ],

        "suggested_questions": [
            "💰 What was our total revenue this month?",
            "🏆 Which products are top sellers?",
            "📦 What is our current inventory status?",
            "👥 Who are our top customers?",
            "🔮 Predict sales for next month",
            "📉 Are there any anomalies in orders?",
        ],
    },

    # ── ACCOUNTING / FINANCE ────────────────────────────────────────────────
    "accounting": {
        "label":       "Accounting / Finance",
        "icon":        "📒",
        "color":       "#6f42c1",          # purple

        "kpi_labels": {
            "revenue":      "Total Income",
            "cost":         "Total Expenses",
            "margin":       "Net Margin",
            "transactions": "Transactions",
            "top_entity":   "Top Account",
            "secondary":    "Cash Balance",
        },

        "kpi_units": {
            "revenue":  "$",
            "cost":     "$",
            "margin":   "%",
        },

        "tabs": [
            {"id": "overview",   "label": "Overview",        "icon": "📊"},
            {"id": "revenue",    "label": "Income",          "icon": "💰"},
            {"id": "costs",      "label": "Expenses",        "icon": "💸"},
            {"id": "cashflow",   "label": "Cash Flow",       "icon": "💵"},
            {"id": "yoy",        "label": "Year on Year",    "icon": "📅"},
            {"id": "gst",        "label": "Tax Report",      "icon": "🧾"},
            {"id": "ai_chat",    "label": "AI Insights",     "icon": "🤖"},
            {"id": "upload",     "label": "Upload Data",     "icon": "📤"},
        ],

        "date_col":   "date",
        "amount_col": "amount",
        "cost_col":   "expense",
        "group_col":  "account",
        "item_col":   "description",

        "column_signatures": [
            "account", "debit", "credit", "balance", "journal",
            "ledger", "invoice_no", "payable", "receivable",
            "expense_category", "income_category",
        ],

        "suggested_questions": [
            "💰 What is our total income this month?",
            "💸 What are our biggest expense categories?",
            "💵 What is our current cash position?",
            "📊 Show our P&L summary",
            "🔮 Forecast expenses for next quarter",
            "📈 How has our net margin trended?",
        ],
    },

    # ── GENERIC (fallback) ───────────────────────────────────────────────────
    "generic": {
        "label":       "General Business",
        "icon":        "📈",
        "color":       "#6b7280",          # gray

        "kpi_labels": {
            "revenue":      "Total Revenue",
            "cost":         "Total Costs",
            "margin":       "Margin",
            "transactions": "Transactions",
            "top_entity":   "Top Category",
            "secondary":    "Growth Rate",
        },

        "kpi_units": {
            "revenue":  "$",
            "cost":     "$",
            "margin":   "%",
        },

        "tabs": [
            {"id": "overview",   "label": "Overview",        "icon": "📊"},
            {"id": "revenue",    "label": "Revenue",         "icon": "💰"},
            {"id": "costs",      "label": "Costs",           "icon": "💸"},
            {"id": "yoy",        "label": "Year on Year",    "icon": "📅"},
            {"id": "ai_chat",    "label": "AI Insights",     "icon": "🤖"},
            {"id": "upload",     "label": "Upload Data",     "icon": "📤"},
        ],

        "date_col":   "date",
        "amount_col": "amount",
        "cost_col":   "cost",
        "group_col":  "category",
        "item_col":   "item",

        "column_signatures": [],

        "suggested_questions": [
            "📊 How did we perform last month?",
            "💰 What is our total revenue?",
            "📈 Show the revenue trend",
            "🔮 Predict next quarter revenue",
            "💸 What are our main cost drivers?",
            "⚠️ Are there anomalies in my data?",
        ],
    },
}

# ---------------------------------------------------------------------------
# Format → Domain mapping  (from Schema Agent detection)
# ---------------------------------------------------------------------------

FORMAT_TO_DOMAIN: dict[str, str] = {
    "marg_erp":        "pharmacy",
    "medstar_custom":  "pharmacy",
    "quickbooks":      "accounting",
    "square":          "retail",
    "shopify":         "retail",
    "saas_csv":        "saas",
    "generic_csv":     "generic",
    "custom_csv":      "generic",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_domain_config(domain: str = "pharmacy") -> dict:
    """Return the config dict for the given domain. Falls back to generic."""
    return DOMAIN_CONFIGS.get(domain, DOMAIN_CONFIGS["generic"])


def get_domain_from_format(format_id: str) -> str:
    """Map a Schema Agent format_id to a domain string."""
    return FORMAT_TO_DOMAIN.get(format_id, "generic")


def detect_domain_from_columns(columns: list[str]) -> str:
    """
    Infer domain by scoring column name matches against each domain's signatures.
    Returns the best-matching domain key, or 'generic' if no clear winner.

    Used as a fallback when format detection is ambiguous.
    """
    cols_lower = {c.lower().strip() for c in columns}
    scores: dict[str, int] = {}

    for domain, cfg in DOMAIN_CONFIGS.items():
        if domain == "generic":
            continue
        sigs = set(cfg.get("column_signatures", []))
        scores[domain] = len(cols_lower & sigs)

    if not scores or max(scores.values()) == 0:
        return "generic"

    best = max(scores, key=lambda d: scores[d])
    # Require at least 2 signature matches to be confident
    return best if scores[best] >= 2 else "generic"


def get_kpi_label(domain: str, key: str, fallback: str = "") -> str:
    """Convenience helper — get a single KPI label for a domain."""
    cfg = get_domain_config(domain)
    return cfg.get("kpi_labels", {}).get(key, fallback)


def get_currency_symbol(domain: str) -> str:
    """Return the currency symbol for this domain's primary market."""
    return get_domain_config(domain).get("kpi_units", {}).get("revenue", "₹")


def list_domains() -> list[dict]:
    """Return a summary list of all domains for UI dropdowns."""
    return [
        {"value": key, "label": f"{cfg['icon']} {cfg['label']}"}
        for key, cfg in DOMAIN_CONFIGS.items()
    ]
