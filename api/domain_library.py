"""
api/domain_library.py
InsightHub canonical schema definitions for each supported domain.

A "canonical schema" is the standard column set that InsightHub
understands internally. When a tenant uploads data, their column names
are mapped to these canonical names via SchemaMapping rows.

Supported domains:
  pharmacy  — sales (daily summary) + purchases (GRN lines)
  retail    — sales (transactions)  + inventory (stock levels)
"""

from __future__ import annotations
from typing import Dict, List

# ── Field definition helper ────────────────────────────────────────────────────

def _f(canonical_name, display_name, data_type, category, is_required, description):
    return {
        "canonical_name": canonical_name,
        "display_name":   display_name,
        "data_type":      data_type,
        "category":       category,
        "is_required":    is_required,
        "description":    description,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PHARMACY DOMAIN
# Canonical columns match data_loader.py exactly so MedStar needs no remapping.
# ═══════════════════════════════════════════════════════════════════════════════

PHARMACY_SALES_SCHEMA: List[dict] = [
    _f("bill_date",         "Bill Date",            "date",    "identifier",  True,  "Date of the daily sales summary"),
    _f("net_amount",        "Net Amount",           "float",   "financial",   True,  "Total net sales for the day"),
    _f("cash_bill_count",   "Cash Bill Count",      "integer", "operational", False, "Number of cash bills issued"),
    _f("cash_sales",        "Cash Sales",           "float",   "financial",   False, "Total value of cash sales"),
    _f("credit_bill_count", "Credit Bill Count",    "integer", "operational", False, "Number of credit bills issued"),
    _f("credit_sales",      "Credit Sales",         "float",   "financial",   False, "Total value of credit sales"),
    _f("card_bill_count",   "Card Bill Count",      "integer", "operational", False, "Number of card bills issued"),
    _f("card_sales",        "Card Sales",           "float",   "financial",   False, "Total value of card sales"),
    _f("return_count",      "Return Count",         "integer", "operational", False, "Number of return transactions"),
    _f("cash_return",       "Cash Return",          "float",   "financial",   False, "Value of cash returns"),
    _f("discount",          "Discount",             "float",   "financial",   False, "Total discount given"),
    _f("total_bills",       "Total Bills",          "integer", "operational", False, "Total number of bills issued"),
    _f("pharma_sales",      "Pharma Sales",         "float",   "financial",   False, "Sales from pharmaceutical products"),
    _f("non_pharma_sales",  "Non-Pharma Sales",     "float",   "financial",   False, "Sales from non-pharmaceutical products"),
    _f("cash_in_hand",      "Cash in Hand",         "float",   "financial",   False, "Closing cash balance"),
    _f("cost_of_sales",     "Cost of Sales",        "float",   "financial",   False, "Direct cost of goods sold"),
    _f("value",             "Value",                "float",   "financial",   False, "Gross value before deductions"),
    _f("margin_pct",        "Margin %",             "float",   "financial",   False, "Gross margin percentage"),
]

PHARMACY_PURCHASES_SCHEMA: List[dict] = [
    _f("grn_date",          "GRN Date",             "date",    "identifier",  True,  "Goods receipt note date"),
    _f("grn_number",        "GRN Number",           "string",  "identifier",  False, "Unique GRN reference number"),
    _f("supplier_code",     "Supplier Code",        "string",  "identifier",  False, "Supplier's internal code"),
    _f("supplier_name",     "Supplier Name",        "string",  "identifier",  True,  "Name of the supplier"),
    _f("invoice_number",    "Invoice Number",       "string",  "identifier",  False, "Supplier invoice number"),
    _f("invoice_date",      "Invoice Date",         "date",    "identifier",  False, "Date on supplier invoice"),
    _f("gross_amount",      "Gross Amount",         "float",   "financial",   False, "Gross purchase value before discounts"),
    _f("discount_pct",      "Discount %",           "float",   "financial",   False, "Discount percentage from supplier"),
    _f("adjustment_value",  "Adjustment Value",     "float",   "financial",   False, "Manual adjustment to invoice value"),
    _f("net_amount",        "Net Amount",           "float",   "financial",   True,  "Final net purchase value"),
    _f("vat_amount",        "VAT Amount",           "float",   "gst",         False, "VAT charged on purchase"),
    _f("base_amount",       "Base Amount",          "float",   "financial",   False, "Taxable base amount"),
    _f("sgst",              "SGST",                 "float",   "gst",         False, "State GST amount"),
    _f("cgst",              "CGST",                 "float",   "gst",         False, "Central GST amount"),
    _f("igst",              "IGST",                 "float",   "gst",         False, "Integrated GST amount"),
    _f("total_gst",         "Total GST",            "float",   "gst",         False, "Total GST (SGST + CGST + IGST)"),
    _f("amount",            "Amount",               "float",   "financial",   False, "Final amount payable"),
    _f("dealer_type",       "Dealer Type",          "string",  "identifier",  False, "Type of dealer (local/interstate)"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# RETAIL DOMAIN
# Canonical schema for general retail — Sales + Inventory
# ═══════════════════════════════════════════════════════════════════════════════

RETAIL_SALES_SCHEMA: List[dict] = [
    _f("transaction_date",  "Transaction Date",     "date",    "identifier",  True,  "Date of the sales transaction"),
    _f("transaction_id",    "Transaction ID",       "string",  "identifier",  False, "Unique transaction reference"),
    _f("product_code",      "Product Code",         "string",  "identifier",  False, "SKU or product code"),
    _f("product_name",      "Product Name",         "string",  "identifier",  False, "Name of product sold"),
    _f("category",          "Category",             "string",  "identifier",  False, "Product category"),
    _f("quantity_sold",     "Quantity Sold",        "integer", "operational", True,  "Number of units sold"),
    _f("unit_price",        "Unit Price",           "float",   "financial",   True,  "Selling price per unit"),
    _f("gross_amount",      "Gross Amount",         "float",   "financial",   False, "Quantity × unit price before discounts"),
    _f("discount_amount",   "Discount Amount",      "float",   "financial",   False, "Discount applied on the transaction"),
    _f("net_amount",        "Net Amount",           "float",   "financial",   True,  "Final amount after discounts"),
    _f("cost_price",        "Cost Price",           "float",   "financial",   False, "Cost of goods per unit"),
    _f("gross_profit",      "Gross Profit",         "float",   "financial",   False, "Net amount minus total cost"),
    _f("payment_method",    "Payment Method",       "string",  "operational", False, "Cash / Card / UPI / Credit"),
    _f("customer_id",       "Customer ID",          "string",  "identifier",  False, "Customer reference (optional)"),
    _f("store_id",          "Store ID",             "string",  "identifier",  False, "Branch or store identifier"),
    _f("cashier_id",        "Cashier ID",           "string",  "identifier",  False, "Staff who processed the sale"),
    _f("return_flag",       "Return Flag",          "string",  "operational", False, "Y/N — whether this is a return"),
    _f("tax_amount",        "Tax Amount",           "float",   "gst",         False, "Tax charged on the transaction"),
]

RETAIL_INVENTORY_SCHEMA: List[dict] = [
    _f("snapshot_date",     "Snapshot Date",        "date",    "identifier",  True,  "Date of the inventory snapshot"),
    _f("product_code",      "Product Code",         "string",  "identifier",  True,  "SKU or product code"),
    _f("product_name",      "Product Name",         "string",  "identifier",  False, "Name of product"),
    _f("category",          "Category",             "string",  "identifier",  False, "Product category"),
    _f("store_id",          "Store ID",             "string",  "identifier",  False, "Branch or store identifier"),
    _f("opening_stock",     "Opening Stock",        "integer", "operational", False, "Units on hand at start of period"),
    _f("received_qty",      "Received Qty",         "integer", "operational", False, "Units received (GRN)"),
    _f("sold_qty",          "Sold Qty",             "integer", "operational", False, "Units sold in the period"),
    _f("return_qty",        "Return Qty",           "integer", "operational", False, "Units returned by customers"),
    _f("closing_stock",     "Closing Stock",        "integer", "operational", True,  "Units on hand at end of period"),
    _f("reorder_level",     "Reorder Level",        "integer", "operational", False, "Minimum stock before reorder"),
    _f("unit_cost",         "Unit Cost",            "float",   "financial",   False, "Cost price per unit"),
    _f("stock_value",       "Stock Value",          "float",   "financial",   False, "Closing stock × unit cost"),
    _f("expiry_date",       "Expiry Date",          "date",    "identifier",  False, "Earliest expiry date in batch (if applicable)"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Registry — single source of truth
# ═══════════════════════════════════════════════════════════════════════════════

DOMAIN_REGISTRY: Dict[str, Dict[str, List[dict]]] = {
    "pharmacy": {
        "sales":     PHARMACY_SALES_SCHEMA,
        "purchases": PHARMACY_PURCHASES_SCHEMA,
    },
    "retail": {
        "sales":     RETAIL_SALES_SCHEMA,
        "inventory": RETAIL_INVENTORY_SCHEMA,
    },
}

# All available domains
AVAILABLE_DOMAINS = list(DOMAIN_REGISTRY.keys())

# Default modules seeded for every new tenant
DEFAULT_MODULES = [
    "sales_analytics",
    "purchase_analytics",
    "pdf_reports",
    "data_upload",
    "threshold_alerts",
    "branch_compare",
]

# MedStar default schema mappings (source = canonical since data_loader already uses canonical names)
MEDSTAR_DEFAULT_MAPPINGS = [
    {"entity": "sales",     "source_column": col["canonical_name"], "canonical_column": col["canonical_name"]}
    for col in PHARMACY_SALES_SCHEMA
] + [
    {"entity": "purchases", "source_column": col["canonical_name"], "canonical_column": col["canonical_name"]}
    for col in PHARMACY_PURCHASES_SCHEMA
]


def get_domain_schema(domain_type: str, entity: str) -> List[dict]:
    """Return canonical field list for a domain + entity, or empty list."""
    return DOMAIN_REGISTRY.get(domain_type, {}).get(entity, [])


def get_canonical_names(domain_type: str, entity: str) -> List[str]:
    """Return just the canonical column names for a domain + entity."""
    return [f["canonical_name"] for f in get_domain_schema(domain_type, entity)]


def get_canonical_schema(domain_type: str, entity: str):
    """Alias for get_domain_schema — returns field list or None if unknown."""
    key_domain = DOMAIN_REGISTRY.get(domain_type)
    if key_domain is None:
        return None
    return key_domain.get(entity)
