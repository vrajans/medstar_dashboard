"""
ai/anomaly.py — US-304
Pure Z-score anomaly detection engine (no LLM, no external deps beyond pandas/numpy).

Detects daily revenue / cost outliers using a rolling Z-score window.
Works for any metric: sales, purchases, margin, order count.

Public API
----------
detect_revenue_anomalies(sales_df, threshold=2.5, window=30) -> list[dict]
detect_cost_anomalies(purchase_df, threshold=2.5, window=30) -> list[dict]
detect_all(sales_df, purchase_df, threshold=2.5, window=30)  -> list[dict]
render_anomaly_banners(anomalies)                             -> dash html.Div

Each anomaly dict has:
  date        str  YYYY-MM-DD
  type        str  spike | drop | gap | margin_outlier
  metric      str  revenue | cost | margin | orders
  value       float
  z_score     float
  direction   str  above | below
  description str  human-readable sentence
"""

from __future__ import annotations

import logging
from datetime import timedelta, date as date_
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_THRESHOLD = 2.5   # |z| must exceed this to flag an anomaly
_DEFAULT_WINDOW    = 30    # rolling window size in days
_MAX_ANOMALIES     = 10    # cap results to avoid flooding UI
_MIN_SERIES_LEN    = 5     # need at least this many points for meaningful Z-score


# ─────────────────────────────────────────────────────────────────────────────
# Core Z-score engine
# ─────────────────────────────────────────────────────────────────────────────

def _zscore_anomalies(
    series: pd.Series,     # daily aggregated values, index = date
    metric: str,           # label for the metric (e.g. "revenue")
    threshold: float,
    window: int,
    label_singular: str = "",  # e.g. "₹" or "%"
    fmt_fn=None,
) -> list[dict]:
    """
    Core Z-score detector on a date-indexed pandas Series.
    Uses a rolling mean/std to flag points where |z| > threshold.
    """
    if series is None or len(series) < _MIN_SERIES_LEN:
        return []

    fmt_fn = fmt_fn or (lambda v: f"{v:,.0f}")

    # Clip window to series length
    w = min(window, len(series))
    roll_mean = series.rolling(w, min_periods=3, center=False).mean()
    roll_std  = series.rolling(w, min_periods=3, center=False).std(ddof=0)

    anomalies = []
    for dt, val in series.items():
        mu  = roll_mean.get(dt)
        sig = roll_std.get(dt)
        if mu is None or sig is None or np.isnan(mu) or np.isnan(sig) or sig == 0:
            continue
        z = (val - mu) / sig
        if abs(z) > threshold:
            direction = "above" if z > 0 else "below"
            atype     = "spike" if z > 0 else "drop"
            anomalies.append({
                "date":        str(dt)[:10],
                "type":        atype,
                "metric":      metric,
                "value":       float(val),
                "z_score":     round(float(z), 2),
                "direction":   direction,
                "description": (
                    f"{metric.title()} of {label_singular}{fmt_fn(val)} on {str(dt)[:10]} "
                    f"is {abs(z):.1f}σ {direction} the {w}-day rolling average "
                    f"({label_singular}{fmt_fn(mu)})."
                ),
            })

    # Sort by |z_score| descending, cap
    anomalies.sort(key=lambda a: abs(a["z_score"]), reverse=True)
    return anomalies[:_MAX_ANOMALIES]


def _daily_agg(df: pd.DataFrame, date_col: str, value_col: str) -> Optional[pd.Series]:
    """Aggregate a DataFrame to a daily total Series (date-indexed)."""
    try:
        if df is None or df.empty:
            return None
        if date_col not in df.columns or value_col not in df.columns:
            return None
        tmp = df.copy()
        tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
        tmp = tmp.dropna(subset=[date_col])
        tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce").fillna(0)
        agg = tmp.groupby(date_col)[value_col].sum()
        return agg.sort_index()
    except Exception as exc:
        logger.error("[anomaly] _daily_agg failed (%s / %s): %s", date_col, value_col, exc)
        return None


def _detect_gaps(series: pd.Series, metric: str, max_gap_days: int = 3) -> list[dict]:
    """Flag date gaps longer than max_gap_days (missing data / store closed)."""
    if series is None or len(series) < 2:
        return []
    dates   = pd.to_datetime(series.index).sort_values()
    gaps    = []
    for i in range(1, len(dates)):
        delta = (dates[i] - dates[i - 1]).days
        if delta > max_gap_days:
            gaps.append({
                "date":        str(dates[i])[:10],
                "type":        "gap",
                "metric":      metric,
                "value":       float(delta),
                "z_score":     0.0,
                "direction":   "—",
                "description": (
                    f"No {metric} data for {delta} day(s) between "
                    f"{str(dates[i-1])[:10]} and {str(dates[i])[:10]}."
                ),
            })
    gaps.sort(key=lambda g: g["value"], reverse=True)
    return gaps[:3]


# ─────────────────────────────────────────────────────────────────────────────
# Public detectors
# ─────────────────────────────────────────────────────────────────────────────

def detect_revenue_anomalies(
    sales_df: pd.DataFrame,
    threshold: float = _DEFAULT_THRESHOLD,
    window:    int   = _DEFAULT_WINDOW,
) -> list[dict]:
    """Detect daily revenue spikes / drops via Z-score."""
    series = _daily_agg(sales_df, "bill_date", "net_amount")
    if series is None:
        return []
    anomalies = _zscore_anomalies(series, "revenue", threshold, window,
                                  label_singular="", fmt_fn=lambda v: f"{v:,.0f}")
    anomalies += _detect_gaps(series, "revenue")
    return anomalies


def detect_cost_anomalies(
    purchase_df: pd.DataFrame,
    threshold: float = _DEFAULT_THRESHOLD,
    window:    int   = _DEFAULT_WINDOW,
) -> list[dict]:
    """Detect daily cost spikes / drops via Z-score."""
    series = _daily_agg(purchase_df, "grn_date", "net_amount")
    if series is None:
        return []
    return _zscore_anomalies(series, "cost", threshold, window,
                             label_singular="", fmt_fn=lambda v: f"{v:,.0f}")


def detect_margin_anomalies(
    sales_df: pd.DataFrame,
    threshold: float = _DEFAULT_THRESHOLD,
    window:    int   = _DEFAULT_WINDOW,
) -> list[dict]:
    """Detect daily margin % outliers via Z-score."""
    if sales_df is None or "margin_pct" not in sales_df.columns:
        return []
    series = _daily_agg(sales_df, "bill_date", "margin_pct")
    if series is None:
        return []
    return _zscore_anomalies(series, "margin", threshold, window,
                             label_singular="", fmt_fn=lambda v: f"{v:.1f}%")


def detect_all(
    sales_df:    pd.DataFrame,
    purchase_df: pd.DataFrame,
    threshold:   float = _DEFAULT_THRESHOLD,
    window:      int   = _DEFAULT_WINDOW,
) -> list[dict]:
    """Run all detectors and return a combined, deduplicated anomaly list."""
    results = []
    results.extend(detect_revenue_anomalies(sales_df,    threshold, window))
    results.extend(detect_cost_anomalies(purchase_df,    threshold, window))
    results.extend(detect_margin_anomalies(sales_df,     threshold, window))
    # Deduplicate by (date, metric)
    seen = set()
    deduped = []
    for a in results:
        key = (a["date"], a["metric"])
        if key not in seen:
            seen.add(key)
            deduped.append(a)
    return deduped


# ─────────────────────────────────────────────────────────────────────────────
# Alerting (fire-and-forget)
# ─────────────────────────────────────────────────────────────────────────────

def maybe_send_anomaly_alerts(
    anomalies: list[dict],
    tenant_id: Optional[int],
    tenant_name: str,
    engine,
) -> None:
    """
    For each anomaly flagged |z| > 3.0 (high severity), fire an alert
    through the tenant's configured alert channels.
    Safe to call in a scheduled job — never raises.
    """
    try:
        from alerts import send_multi_channel, get_tenant_channels
        if not tenant_id or not anomalies:
            return
        channels = get_tenant_channels(tenant_id, engine)
        if not channels:
            return
        # Only alert on the highest-severity anomaly
        high = [a for a in anomalies if abs(a.get("z_score", 0)) > 3.0]
        if not high:
            return
        top = high[0]
        subject = (
            f"⚠️ InsightHub Alert — {top['type'].title()} in {top['metric']} "
            f"for {tenant_name} on {top['date']}"
        )
        body = (
            f"{top['description']}\n\n"
            f"Detected {len(high)} high-severity anomalies. "
            f"Log in to InsightHub to review your data.\n\n"
            f"— InsightHub Analytics"
        )
        send_multi_channel(channels, subject, body, tenant_name=tenant_name,
                           alert_level="danger")
    except Exception as exc:
        logger.error("[anomaly] alert send failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Dash rendering helpers
# ─────────────────────────────────────────────────────────────────────────────

# colour constants (keep in sync with Design System v3)
_C_RED    = "#DC2626"
_C_AMBER  = "#D97706"
_C_GREEN  = "#059669"
_C_NAVY   = "#1E293B"
_C_MUTED  = "#64748B"

_TYPE_COLOR = {
    "spike":          _C_AMBER,
    "drop":           _C_RED,
    "gap":            _C_AMBER,
    "margin_outlier": _C_RED,
}
_TYPE_ICON = {
    "spike": "↑",
    "drop":  "↓",
    "gap":   "⚠",
    "margin_outlier": "⚠",
}


def render_anomaly_banners(anomalies: list[dict], max_show: int = 3):
    """
    Render compact anomaly alert banners for the Overview tab.
    Returns a dash html.Div (empty div if no anomalies).
    """
    from dash import html

    if not anomalies:
        return html.Div()

    # Limit displayed anomalies
    shown = anomalies[:max_show]
    extra = len(anomalies) - max_show

    items = []
    for a in shown:
        color = _TYPE_COLOR.get(a.get("type", "spike"), _C_AMBER)
        icon  = _TYPE_ICON.get(a.get("type", "spike"), "⚠")
        z_str = f" (z={a.get('z_score', 0):+.1f})" if a.get("z_score") else ""
        items.append(
            html.Div(
                [
                    html.Span(
                        f"{icon} {a.get('metric','?').title()} anomaly — {a.get('date','?')}",
                        style={"fontWeight": 700, "fontSize": "0.8rem",
                               "color": color, "minWidth": "220px"},
                    ),
                    html.Span(
                        a.get("description", "") + z_str,
                        style={"fontSize": "0.78rem", "color": "#CBD5E1",
                               "flex": 1},
                    ),
                ],
                style={
                    "display": "flex", "gap": "12px", "alignItems": "flex-start",
                    "padding": "8px 14px",
                    "borderBottom": "1px solid rgba(255,255,255,0.04)",
                },
            )
        )

    if extra > 0:
        items.append(
            html.Div(
                f"… and {extra} more anomal{'ies' if extra != 1 else 'y'}. "
                "Check the AI Chat tab for a full analysis.",
                style={
                    "fontSize": "0.74rem", "color": _C_MUTED,
                    "padding": "6px 14px",
                },
            )
        )

    return html.Div(
        [
            html.Div(
                [
                    html.Span("⚠️", style={"fontSize": "1rem", "marginRight": "6px"}),
                    html.Span(
                        f"{len(anomalies)} Anomal{'ies' if len(anomalies) != 1 else 'y'} Detected",
                        style={"fontWeight": 700, "fontSize": "0.85rem", "color": _C_AMBER},
                    ),
                    html.Span(
                        " — Z-score threshold ±2.5σ",
                        style={"fontSize": "0.72rem", "color": _C_MUTED, "marginLeft": "6px"},
                    ),
                ],
                style={
                    "padding": "8px 14px",
                    "borderBottom": "1px solid rgba(255,255,255,0.06)",
                    "display": "flex", "alignItems": "center",
                },
            ),
            *items,
        ],
        style={
            "background": "rgba(217,119,6,0.08)",
            "border": f"1px solid rgba(217,119,6,0.30)",
            "borderLeft": f"4px solid {_C_AMBER}",
            "borderRadius": "8px",
            "marginBottom": "1rem",
            "overflow": "hidden",
        },
    )
