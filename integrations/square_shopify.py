"""
integrations/square_shopify.py  —  Square & Shopify CSV Parsers
================================================================
Parses exported CSV/Excel files from Square POS and Shopify admin
and converts them to InsightHub's canonical sales schema.

Usage
-----
from integrations.square_shopify import parse_square_csv, parse_shopify_csv
import pandas as pd

df = parse_square_csv("square_transactions.csv")
df = parse_shopify_csv("shopify_orders.csv")
"""

import pandas as pd
import logging
from datetime import datetime
from typing import Optional, Any

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# SQUARE
# ═════════════════════════════════════════════════════════════════════════════

# Square CSV column → canonical column mapping
SQUARE_COL_MAP = {
    # Transaction export columns
    "Date":                      "bill_date",
    "Time":                      "_time",
    "Category":                  "category",
    "Item":                      "item_name",
    "Qty":                       "quantity",
    "Price Point Name":          "_price_tier",
    "SKU":                       "sku",
    "Modifiers Applied":         "_modifiers",
    "Gross Sales":               "gross_amount",
    "Discounts":                 "_discounts",
    "Net Sales":                 "net_amount",
    "Tax":                       "tax_amount",
    "Transaction ID":            "transaction_id",
    "Payment ID":                "_payment_id",
    "Device Name":               "branch",
    "Notes":                     "_notes",
    "Details":                   "_details",
    "Event Type":                "_event_type",
    "Location":                  "branch",
    "Dining Option":             "_dining_opt",
    "Customer ID":               "_customer_id",
    "Customer Name":             "_customer_name",
    "Customer Reference ID":     "_cust_ref",
    "Device Nickname":           "branch",
    "Staff Name":                "_staff",
    "Unit":                      "unit",
    "Count":                     "quantity",
    "Payment Type":              "_payment_type",
    "Partial Refunds":           "_refund",
    "Fees":                      "_fees",
    "Net Total":                 "net_amount",
}

SQUARE_CASH_METHODS = {"cash", "check", "cheque"}
SQUARE_CARD_METHODS = {"card", "credit card", "debit card", "contactless", "apple pay",
                       "google pay", "visa", "mastercard", "amex"}


def parse_square_csv(filepath_or_bytes, encoding: str = "utf-8") -> pd.DataFrame:
    """
    Parse a Square transaction export CSV.
    Returns DataFrame in InsightHub sales schema.
    """
    try:
        if isinstance(filepath_or_bytes, (str, bytes)):
            df_raw = pd.read_csv(filepath_or_bytes, encoding=encoding, low_memory=False)
        else:
            df_raw = pd.read_csv(filepath_or_bytes, low_memory=False)
    except Exception as exc:
        logger.error("[square] Could not read CSV: %s", exc)
        return pd.DataFrame()

    # Rename columns
    df = df_raw.rename(columns={k: v for k, v in SQUARE_COL_MAP.items() if k in df_raw.columns})

    # Normalise date
    if "bill_date" in df.columns:
        df["bill_date"] = pd.to_datetime(df["bill_date"], errors="coerce").dt.date

    # Normalise amounts — Square exports with $ or commas
    for col in ["net_amount", "gross_amount", "tax_amount"]:
        if col in df.columns:
            df[col] = (df[col].astype(str)
                       .str.replace(r"[$,()]", "", regex=True)
                       .str.strip()
                       .replace("", "0"))
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            # Square sometimes exports negative amounts for refunds — make positive
            df[col] = df[col].abs()

    # Payment mode split
    if "_payment_type" in df.columns:
        pay = df["_payment_type"].str.lower().fillna("")
        df["cash_sales"]   = df.get("net_amount", 0).where(pay.isin(SQUARE_CASH_METHODS), 0)
        df["credit_sales"] = df.get("net_amount", 0).where(
            ~pay.isin(SQUARE_CASH_METHODS | SQUARE_CARD_METHODS), 0)
        df["card_sales"]   = df.get("net_amount", 0).where(pay.isin(SQUARE_CARD_METHODS), 0)

    # Drop internal columns
    drop = [c for c in df.columns if c.startswith("_")]
    df   = df.drop(columns=drop, errors="ignore")

    # Ensure required columns exist
    for col in ["net_amount","bill_date","branch"]:
        if col not in df.columns:
            df[col] = None

    df["source"] = "square"
    logger.info("[square] Parsed %d rows from Square export.", len(df))
    return df


# ═════════════════════════════════════════════════════════════════════════════
# SHOPIFY
# ═════════════════════════════════════════════════════════════════════════════

SHOPIFY_COL_MAP = {
    "Name":                     "transaction_id",
    "Email":                    "_customer_email",
    "Financial Status":         "_fin_status",
    "Paid at":                  "bill_date",
    "Fulfillment Status":       "_fulfill_status",
    "Fulfilled at":             "_fulfilled_at",
    "Currency":                 "currency",
    "Subtotal":                 "sub_total",
    "Shipping":                 "_shipping",
    "Taxes":                    "tax_amount",
    "Total":                    "net_amount",
    "Discount Code":            "_discount_code",
    "Discount Amount":          "_discount_amount",
    "Shipping Method":          "_ship_method",
    "Created at":               "_created_at",
    "Lineitem quantity":        "quantity",
    "Lineitem name":            "item_name",
    "Lineitem price":           "_item_price",
    "Lineitem compare at price":"_compare_price",
    "Lineitem sku":             "sku",
    "Lineitem requires shipping":"_requires_ship",
    "Lineitem taxable":         "_taxable",
    "Lineitem fulfillment status":"_item_fulfill",
    "Billing Name":             "_billing_name",
    "Billing Street":           "_billing_street",
    "Billing Address1":         "_billing_addr1",
    "Billing Address2":         "_billing_addr2",
    "Billing Company":          "_company",
    "Billing City":             "_city",
    "Billing Zip":              "_zip",
    "Billing Province":         "state",    # US state for tax purposes
    "Billing Country":          "country",
    "Billing Phone":            "_phone",
    "Shipping Name":            "_ship_name",
    "Payment Method":           "_payment_method",
    "Payment Reference":        "_pay_ref",
    "Refunded Amount":          "_refunded",
    "Vendor":                   "supplier_name",
    "Id":                       "_order_id",
    "Tags":                     "_tags",
    "Risk Level":               "_risk",
    "Source":                   "_channel",
    "Location":                 "branch",
    "Payment ID":               "_pay_id",
    "Receipt Number":           "_receipt",
}


def parse_shopify_csv(filepath_or_bytes, encoding: str = "utf-8") -> pd.DataFrame:
    """
    Parse a Shopify orders export CSV.
    Returns DataFrame in InsightHub sales schema.

    Shopify exports one row per line item; this function aggregates to order level.
    """
    try:
        if isinstance(filepath_or_bytes, (str, bytes)):
            df_raw = pd.read_csv(filepath_or_bytes, encoding=encoding, low_memory=False)
        else:
            df_raw = pd.read_csv(filepath_or_bytes, low_memory=False)
    except Exception as exc:
        logger.error("[shopify] Could not read CSV: %s", exc)
        return pd.DataFrame()

    df = df_raw.rename(columns={k: v for k, v in SHOPIFY_COL_MAP.items() if k in df_raw.columns})

    # Filter to paid/completed orders only
    if "_fin_status" in df.columns:
        df = df[df["_fin_status"].str.lower().fillna("").isin(["paid", "partially_paid", ""])]

    # Normalise date
    if "bill_date" in df.columns:
        df["bill_date"] = pd.to_datetime(df["bill_date"], errors="coerce").dt.date

    # Normalise amounts
    for col in ["net_amount","sub_total","tax_amount","_discount_amount","_refunded"]:
        if col in df.columns:
            df[col] = (df[col].astype(str)
                       .str.replace(r"[$,]", "", regex=True)
                       .replace("", "0"))
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Aggregate: Shopify exports multi-row per order (one per line item)
    # Use transaction_id to group, take the "Total" from the first row per order
    if "transaction_id" in df.columns:
        # Deduplicate: keep order-level fields from first occurrence
        order_cols = [c for c in ["transaction_id","bill_date","net_amount","tax_amount",
                                   "sub_total","state","country","currency","branch","source"]
                      if c in df.columns]
        df_orders = df[order_cols].drop_duplicates(subset=["transaction_id"], keep="first")
    else:
        df_orders = df.copy()

    # Payment mode — Shopify doesn't distinguish cash/card in standard export
    # but we can infer from payment method column if present
    if "_payment_method" in df_orders.columns:
        pay = df_orders["_payment_method"].str.lower().fillna("")
        df_orders["cash_sales"]   = df_orders.get("net_amount", 0).where(pay.str.contains("cash"), 0)
        df_orders["card_sales"]   = df_orders.get("net_amount", 0).where(
            pay.str.contains("card|visa|master|amex|paypal|stripe"), 0)
        df_orders["credit_sales"] = 0  # Shopify = usually card payments
    else:
        df_orders["card_sales"]   = df_orders.get("net_amount", 0)
        df_orders["cash_sales"]   = 0
        df_orders["credit_sales"] = 0

    # Drop internal cols
    drop = [c for c in df_orders.columns if c.startswith("_")]
    df_orders = df_orders.drop(columns=drop, errors="ignore")

    for col in ["net_amount","bill_date","branch"]:
        if col not in df_orders.columns:
            df_orders[col] = None

    df_orders["source"] = "shopify"
    logger.info("[shopify] Parsed %d orders from Shopify export.", len(df_orders))
    return df_orders


# ═════════════════════════════════════════════════════════════════════════════
# Generic CSV auto-detect
# ═════════════════════════════════════════════════════════════════════════════

def detect_and_parse_csv(filepath_or_bytes) -> tuple[Optional[pd.DataFrame], str]:
    """
    Auto-detect whether a CSV is Square or Shopify and parse accordingly.
    Returns (DataFrame, source_name) or (None, "unknown").
    """
    try:
        header = pd.read_csv(filepath_or_bytes, nrows=0).columns.tolist()
    except Exception:
        return None, "unknown"

    header_set = set(h.strip() for h in header)

    # Square fingerprint
    if any(c in header_set for c in ["Device Name", "Device Nickname", "Gross Sales", "Net Sales"]):
        df = parse_square_csv(filepath_or_bytes)
        return df, "square"

    # Shopify fingerprint
    if any(c in header_set for c in ["Financial Status", "Fulfillment Status",
                                      "Lineitem quantity", "Paid at"]):
        df = parse_shopify_csv(filepath_or_bytes)
        return df, "shopify"

    logger.warning("[csv_detect] Could not identify CSV as Square or Shopify.")
    return None, "unknown"


def save_integration_data(df: pd.DataFrame, source: str,
                          engine: Any, tenant_id: int) -> tuple[bool, str]:
    """Save parsed integration CSV data to the DB."""
    if df is None or df.empty:
        return False, "No data to save."
    try:
        df = df.copy()
        df["tenant_id"] = tenant_id
        df["source"]    = source

        # Remove old rows from this source for this tenant
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM sales_data WHERE tenant_id=:tid AND source=:src
            """), {"tid": tenant_id, "src": source})
            df.to_sql("sales_data", conn, if_exists="append", index=False)

        return True, f"Saved {len(df)} rows from {source.title()} import."
    except Exception as exc:
        logger.error("[square_shopify] save_integration_data: %s", exc)
        return False, f"Save failed: {exc}"
