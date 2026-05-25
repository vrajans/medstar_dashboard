"""
pdf_report.py  — MedStar / InsightHub branded PDF export
Uses reportlab (pure-Python, no system deps).
Entry point:  generate_pdf(sales_df, purchase_df, start_date, end_date, branch, fmt_inr)
Returns:       bytes  (PDF binary ready for dcc.send_bytes)
"""

from io import BytesIO
from datetime import date
import pandas as pd

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether,
)
from reportlab.lib import colors

# ── Brand colours ──────────────────────────────────────────────
GREEN      = HexColor("#1e7e4b")
GREEN_LIGHT= HexColor("#e8f5e9")
GREEN_MID  = HexColor("#a8d5a8")
BLUE       = HexColor("#0d6efd")
ORANGE     = HexColor("#fd7e14")
GREY_BG    = HexColor("#f1f5f9")
GREY_TEXT  = HexColor("#475569")
GREY_MUTED = HexColor("#94a3b8")

PAGE_W, PAGE_H = A4
MARGIN = 1.8 * cm


def _styles():
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("H1", parent=base["Normal"],
                             fontSize=18, textColor=white, leading=22,
                             spaceAfter=0, spaceBefore=0),
        "sub": ParagraphStyle("Sub", parent=base["Normal"],
                              fontSize=9, textColor=GREY_MUTED, leading=13),
        "h2": ParagraphStyle("H2", parent=base["Normal"],
                             fontSize=11, textColor=GREEN,
                             fontName="Helvetica-Bold",
                             spaceBefore=14, spaceAfter=4),
        "body": ParagraphStyle("Body", parent=base["Normal"],
                               fontSize=8.5, textColor=GREY_TEXT, leading=12),
        "small": ParagraphStyle("Small", parent=base["Normal"],
                                fontSize=7.5, textColor=GREY_MUTED, leading=11),
        "kpi_val": ParagraphStyle("KpiVal", parent=base["Normal"],
                                  fontSize=15, textColor=GREEN,
                                  fontName="Helvetica-Bold", leading=18),
        "kpi_lbl": ParagraphStyle("KpiLbl", parent=base["Normal"],
                                  fontSize=7.5, textColor=GREY_MUTED, leading=10),
    }


def _tbl_style_base():
    return [
        ("BACKGROUND",   (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR",    (0, 0), (-1, 0), white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 8),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [white, GREY_BG]),
        ("FONTSIZE",     (0, 1), (-1, -1), 7.5),
        ("TEXTCOLOR",    (0, 1), (-1, -1), GREY_TEXT),
        ("GRID",         (0, 0), (-1, -1), 0.4, HexColor("#e2e8f0")),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
    ]


def _kpi_block(kpi_data, styles):
    """kpi_data: list of (label, value) tuples, max 6."""
    col_w = (PAGE_W - 2*MARGIN) / len(kpi_data)
    cells_lbl = [Paragraph(lbl, styles["kpi_lbl"]) for lbl, _ in kpi_data]
    cells_val = [Paragraph(val, styles["kpi_val"]) for _, val in kpi_data]
    tbl = Table([cells_lbl, cells_val], colWidths=[col_w]*len(kpi_data))
    tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), white),
        ("BOX",         (0, 0), (-1, -1), 0.5, GREEN_MID),
        ("INNERGRID",   (0, 0), (-1, -1), 0.4, HexColor("#e2e8f0")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",(0, 0), (-1, -1), 10),
        ("TOPPADDING",  (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0,0), (-1, -1), 8),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",       (0, 1), (-1, 1),  "LEFT"),
    ]))
    return tbl


def generate_pdf(sales_df, purchase_df, start_date, end_date, branch, fmt_inr):
    """
    Generate a branded A4 PDF report.
    Returns raw bytes.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
    )
    st = _styles()
    story = []
    avail_w = PAGE_W - 2 * MARGIN

    # ── Header band ───────────────────────────────────────────
    period_str = "{} to {}".format(
        pd.to_datetime(start_date).strftime("%d %b %Y") if start_date else "All",
        pd.to_datetime(end_date).strftime("%d %b %Y")   if end_date   else "All",
    )
    branch_str = branch if branch != "All" else "All Branches"
    generated  = date.today().strftime("%d %b %Y")

    hdr_tbl = Table([[
        Paragraph("MedStar Pharmacy — Analytics Report", st["h1"]),
        Paragraph(
            "Period: {}  |  Branch: {}  |  Generated: {}".format(
                period_str, branch_str, generated),
            ParagraphStyle("HdrSub", parent=st["sub"],
                           textColor=HexColor("#a8d5a8"), alignment=2)),
    ]], colWidths=[avail_w * 0.6, avail_w * 0.4])
    hdr_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), GREEN),
        ("LEFTPADDING",  (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING",   (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(hdr_tbl)
    story.append(Spacer(1, 10))

    # ── KPI strip ─────────────────────────────────────────────
    story.append(Paragraph("Key Performance Indicators", st["h2"]))

    sales_val  = sales_df["net_amount"].sum()    if not sales_df.empty else 0
    purch_val  = purchase_df["net_amount"].sum() if not purchase_df.empty else 0
    avg_daily  = sales_df["net_amount"].mean()   if not sales_df.empty else 0
    avg_margin = sales_df["margin_pct"].mean()   if (not sales_df.empty and "margin_pct" in sales_df.columns) else None
    total_bills= int(sales_df["total_bills"].sum()) if (not sales_df.empty and "total_bills" in sales_df.columns) else 0
    total_gst  = purchase_df["total_gst"].sum() if (not purchase_df.empty and "total_gst" in purchase_df.columns) else 0

    kpis = [
        ("Total Sales",    fmt_inr(sales_val)),
        ("Total Purchase", fmt_inr(purch_val)),
        ("Avg Daily Sales",fmt_inr(avg_daily)),
        ("Avg Margin",     "{:.1f}%".format(avg_margin) if avg_margin is not None else "N/A"),
        ("Total Bills",    "{:,}".format(total_bills)),
        ("GST Paid",       fmt_inr(total_gst)),
    ]
    story.append(_kpi_block(kpis, st))
    story.append(Spacer(1, 12))

    # ── Sales daily summary ───────────────────────────────────
    if not sales_df.empty:
        story.append(Paragraph("Sales Summary — Daily View", st["h2"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=GREEN_MID, spaceAfter=4))

        sd = sales_df.copy()
        for col in sd.select_dtypes("datetime64[ns]").columns:
            sd[col] = sd[col].dt.strftime("%d %b %Y")

        display_cols = ["branch", "bill_date", "net_amount", "total_bills",
                        "margin_pct", "cash_sales", "credit_sales", "card_sales"]
        display_cols = [c for c in display_cols if c in sd.columns]
        col_labels   = {"branch":"Branch","bill_date":"Date","net_amount":"Net Sales",
                        "total_bills":"Bills","margin_pct":"Margin%",
                        "cash_sales":"Cash","credit_sales":"Credit","card_sales":"Card"}

        sd_show = sd[display_cols].sort_values("bill_date" if "bill_date" in display_cols else display_cols[0])
        col_w   = avail_w / len(display_cols)

        rows = [[Paragraph(col_labels.get(c, c), st["small"]) for c in display_cols]]
        for _, row in sd_show.iterrows():
            r = []
            for c in display_cols:
                v = row[c]
                if c == "net_amount":  v = fmt_inr(float(v)) if v else "0"
                elif c == "margin_pct":v = "{:.1f}%".format(float(v)) if v else "0%"
                elif c in ("cash_sales","credit_sales","card_sales"):
                    v = fmt_inr(float(v)) if v else "0"
                else:
                    v = str(v) if v else ""
                r.append(Paragraph(v, st["small"]))
            rows.append(r)

        sales_tbl = Table(rows, colWidths=[col_w]*len(display_cols), repeatRows=1)
        sales_tbl.setStyle(TableStyle(_tbl_style_base()))
        story.append(sales_tbl)
        story.append(Spacer(1, 12))

    # ── Top suppliers ─────────────────────────────────────────
    if not purchase_df.empty and "supplier_name" in purchase_df.columns:
        story.append(Paragraph("Top 15 Suppliers by Purchase Value", st["h2"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=GREEN_MID, spaceAfter=4))

        sup = (purchase_df.groupby(["branch","supplier_name"])["net_amount"]
               .sum().reset_index()
               .sort_values("net_amount", ascending=False).head(15))

        sup_rows = [[Paragraph(h, st["small"]) for h in ["#","Branch","Supplier","Purchase"]]]
        for i, (_, row) in enumerate(sup.iterrows(), 1):
            sup_rows.append([
                Paragraph(str(i), st["small"]),
                Paragraph(str(row["branch"]), st["small"]),
                Paragraph(str(row["supplier_name"]), st["small"]),
                Paragraph(fmt_inr(float(row["net_amount"])), st["small"]),
            ])

        sup_tbl = Table(sup_rows,
                        colWidths=[avail_w*0.06, avail_w*0.2, avail_w*0.55, avail_w*0.19],
                        repeatRows=1)
        sup_tbl.setStyle(TableStyle(_tbl_style_base()))
        story.append(sup_tbl)
        story.append(Spacer(1, 12))

    # ── Purchase GST summary ──────────────────────────────────
    if not purchase_df.empty:
        gst_cols = [c for c in ["branch","month_label","gross_amount","discount_pct",
                                 "net_amount","sgst","cgst","igst","total_gst"]
                    if c in purchase_df.columns]
        if gst_cols:
            story.append(Paragraph("Purchase & GST Summary by Branch", st["h2"]))
            story.append(HRFlowable(width="100%", thickness=0.5, color=GREEN_MID, spaceAfter=4))
            grp_cols = [c for c in ["branch","month_label"] if c in gst_cols]
            num_cols = [c for c in gst_cols if c not in grp_cols]
            gst_grp = purchase_df.groupby(grp_cols)[num_cols].sum().reset_index()

            col_labels2 = {"branch":"Branch","month_label":"Month","gross_amount":"Gross",
                           "discount_pct":"Disc%","net_amount":"Net","sgst":"SGST",
                           "cgst":"CGST","igst":"IGST","total_gst":"Total GST"}
            col_w2 = avail_w / len(gst_cols)
            gst_rows = [[Paragraph(col_labels2.get(c,c), st["small"]) for c in gst_cols]]
            for _, row in gst_grp.iterrows():
                r = []
                for c in gst_cols:
                    v = row[c]
                    if c == "discount_pct":
                        v = "{:.1f}%".format(float(v)) if v else "0%"
                    elif c not in grp_cols:
                        v = fmt_inr(float(v)) if v else "0"
                    else:
                        v = str(v) if v else ""
                    r.append(Paragraph(v, st["small"]))
                gst_rows.append(r)

            gst_tbl = Table(gst_rows, colWidths=[col_w2]*len(gst_cols), repeatRows=1)
            gst_tbl.setStyle(TableStyle(_tbl_style_base()))
            story.append(gst_tbl)

    # ── Footer ────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GREEN_MID))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "InsightHub Analytics Platform  |  Confidential  |  {}".format(generated),
        st["small"]))

    doc.build(story)
    buf.seek(0)
    return buf.read()
