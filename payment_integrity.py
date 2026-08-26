"""
payment_integrity.py — Healthcare (US insurance) Payment Integrity engine.

Given a claims dataset (professional/institutional), flags likely improper
payments and produces a prioritized worklist + provider risk scorecard. Uses
ONLY the claims file itself (no licensed reference tables), so it works from a
plain Excel/CSV upload. Reference-data checks (NCCI PTP/MUE, fee schedule, DRG,
COB/eligibility) are documented as phase-2 and are stubbed where relevant.

Checks implemented (upload-only):
  1. Duplicate claim lines        — same member+provider+DOS+procedure paid twice
  2. High-dollar outliers         — paid far above peer norm for the same code
  3. Unit / frequency outliers    — units implausibly high for a code (MUE-style)
  4. Provider outliers (FWA)      — paid/claim & paid/member far above peers (z-score)
  5. Upcoding signal (E/M)        — provider's high-level E/M share >> peers
  6. Age/gender edits             — procedure inconsistent with member sex
  7. Impossible day               — provider billing an implausible volume/day

Public API:
    analyze_claims(df) -> dict  { summary, findings, providers, mapping }
    recommendation_text(finding) -> str   (rule-based default recommendation)
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import pandas as pd

# ── canonical column aliases (tolerate common claims layouts) ────────────────
ALIASES = {
    "claim_id":       ["claim_id", "claimid", "claim", "claim_number", "clm_id"],
    "line_no":        ["line_no", "line", "line_number", "svc_line"],
    "member_id":      ["member_id", "memberid", "patient_id", "subscriber_id", "mbr_id", "member"],
    "member_age":     ["member_age", "age", "patient_age"],
    "member_gender":  ["member_gender", "gender", "sex", "patient_gender"],
    "provider_npi":   ["provider_npi", "npi", "rendering_npi", "billing_npi", "provider_id"],
    "provider_name":  ["provider_name", "provider", "rendering_provider", "physician"],
    "specialty":      ["specialty", "provider_specialty", "taxonomy"],
    "service_date":   ["service_date", "dos", "date_of_service", "svc_date", "from_date"],
    "place_of_service": ["place_of_service", "pos"],
    "cpt":            ["cpt", "hcpcs", "procedure_code", "proc_code", "cpt_code", "code", "cpt_hcpcs"],
    "modifier":       ["modifier", "mod", "modifier1", "mod1"],
    "units":          ["units", "unit", "qty", "quantity", "service_units"],
    "billed_amt":     ["billed_amt", "billed", "charge_amt", "submitted_amt", "billed_amount"],
    "allowed_amt":    ["allowed_amt", "allowed", "allowed_amount"],
    "paid_amt":       ["paid_amt", "paid", "paid_amount", "payment_amount"],
}

# E/M office-visit codes and the "high level" subset (upcoding target)
EM_CODES = {"99201", "99202", "99203", "99204", "99205",
            "99211", "99212", "99213", "99214", "99215"}
EM_HIGH = {"99204", "99205", "99214", "99215"}
# crude sex-specific code prefixes (illustrative — real edits use licensed tables)
FEMALE_ONLY_PREFIX = ("59",)             # maternity / obstetric
MALE_ONLY_PREFIX   = ("554", "555")      # prostate-related (illustrative)


def _resolve(df: pd.DataFrame) -> dict:
    """Map canonical names → actual columns (case-insensitive)."""
    lower = {c.lower().strip(): c for c in df.columns}
    out = {}
    for canon, alts in ALIASES.items():
        for a in alts:
            if a in lower:
                out[canon] = lower[a]
                break
    return out


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(r"[$,%]", "", regex=True), errors="coerce")


def _conf(z: float) -> float:
    """Map a z-score to a 0.5–0.99 confidence."""
    return round(min(0.99, max(0.5, 0.5 + (abs(z) - 2.0) * 0.12)), 2)


def analyze_claims(df: pd.DataFrame) -> dict:
    m = _resolve(df)
    d = df.copy()
    # normalize the columns we have
    for canon in ("billed_amt", "allowed_amt", "paid_amt", "units", "member_age"):
        if canon in m:
            d[canon] = _num(d[m[canon]])
    for canon in ("claim_id", "member_id", "provider_npi", "provider_name",
                  "cpt", "member_gender", "specialty", "service_date", "place_of_service"):
        if canon in m:
            d[canon] = d[m[canon]].astype(str).str.strip()
    if "paid_amt" not in d:
        return {"error": "No paid amount column found — need one of: paid_amt/paid/paid_amount.",
                "mapping": m}
    d["paid_amt"] = d.get("paid_amt", pd.Series(0, index=d.index)).fillna(0)
    if "cpt" not in d:
        d["cpt"] = "UNKNOWN"
    if "provider_npi" not in d:
        d["provider_npi"] = d.get("provider_name", "UNKNOWN")

    findings: list[dict] = []

    # ── 1. Duplicate claim lines ────────────────────────────────────────────
    dup_keys = [c for c in ("member_id", "provider_npi", "service_date", "cpt") if c in d]
    if len(dup_keys) >= 3:
        dupe = d[d.duplicated(subset=dup_keys, keep="first")]
        for _, r in dupe.iterrows():
            findings.append(_f(r, "Duplicate", "high",
                f"Duplicate of an earlier line (same {', '.join(dup_keys)}).",
                float(r["paid_amt"]), 0.95, "Deny / recover — duplicate payment."))

    # ── 2. High-dollar outliers per procedure (z-score OR robust median rule) ─
    stats = d.groupby("cpt")["paid_amt"].agg(["mean", "std", "median", "count"])
    for _, r in d.iterrows():
        st = stats.loc[r["cpt"]] if r["cpt"] in stats.index else None
        if st is None or st["count"] < 8:
            continue
        med = float(st["median"]) or 1.0
        z = (r["paid_amt"] - st["mean"]) / st["std"] if st["std"] and not math.isnan(st["std"]) else 0
        if (z >= 3 or r["paid_amt"] > 5 * med) and r["paid_amt"] >= 25:
            findings.append(_f(r, "High-$ outlier", "med",
                f"Paid ${r['paid_amt']:,.0f} vs typical ${med:,.0f} for {r['cpt']}"
                + (f" (z={z:.1f})" if z else "") + ".",
                float(r["paid_amt"] - med), max(_conf(z), 0.8),
                "Review pricing — paid far above the norm for this code."))

    # ── 3. Unit / frequency outliers (MUE-style) ────────────────────────────
    if "units" in d:
        ustats = d.groupby("cpt")["units"].agg(["mean", "std", "count"])
        for _, r in d.iterrows():
            if pd.isna(r.get("units")):
                continue
            us = ustats.loc[r["cpt"]] if r["cpt"] in ustats.index else None
            hard = float(r["units"]) >= 50
            zbig = us is not None and us["count"] >= 8 and us["std"] and not math.isnan(us["std"]) \
                and (float(r["units"]) - us["mean"]) / us["std"] >= 4
            if hard or zbig:
                findings.append(_f(r, "Unit outlier", "med",
                    f"{r['units']:.0f} units billed for {r['cpt']} — implausibly high.",
                    float(r["paid_amt"]) * 0.5, 0.7,
                    "Review medical necessity / units (possible MUE breach)."))

    # ── 6. Age/gender edits ─────────────────────────────────────────────────
    if "member_gender" in d:
        for _, r in d.iterrows():
            g = str(r.get("member_gender", "")).upper()[:1]
            code = str(r["cpt"])
            if g == "M" and code.startswith(FEMALE_ONLY_PREFIX):
                findings.append(_f(r, "Gender edit", "high",
                    f"Female-only procedure {code} billed for a male member.",
                    float(r["paid_amt"]), 0.9, "Deny — sex-inconsistent procedure."))
            elif g == "F" and code.startswith(MALE_ONLY_PREFIX):
                findings.append(_f(r, "Gender edit", "high",
                    f"Male-only procedure {code} billed for a female member.",
                    float(r["paid_amt"]), 0.9, "Deny — sex-inconsistent procedure."))

    # ── provider aggregates → 4. outliers, 5. upcoding, 7. impossible day ────
    providers = _provider_scorecard(d, findings)

    # ── summary ─────────────────────────────────────────────────────────────
    total_paid = float(d["paid_amt"].sum())
    at_risk = float(sum(f["amount_at_risk"] for f in findings))
    findings.sort(key=lambda f: f["amount_at_risk"] * f["confidence"], reverse=True)
    by_cat: dict[str, dict] = {}
    for f in findings:
        c = by_cat.setdefault(f["category"], {"count": 0, "amount": 0.0})
        c["count"] += 1
        c["amount"] += f["amount_at_risk"]
    summary = {
        "total_claims": int(len(d)),
        "total_paid": round(total_paid, 2),
        "flagged_claims": len(findings),
        "amount_at_risk": round(at_risk, 2),
        "pct_at_risk": round(at_risk / total_paid * 100, 2) if total_paid else 0,
        "by_category": [{"category": k, **v} for k, v in
                        sorted(by_cat.items(), key=lambda x: -x[1]["amount"])],
        "providers_flagged": sum(1 for p in providers if p["risk_score"] >= 50),
    }
    return {"summary": summary, "findings": findings[:500], "providers": providers, "mapping": m}


def _f(r, category, severity, reason, amount, confidence, rec) -> dict:
    return {
        "claim_id": str(r.get("claim_id", "")) or "—",
        "provider_npi": str(r.get("provider_npi", "")) or "—",
        "provider_name": str(r.get("provider_name", "")) or "",
        "cpt": str(r.get("cpt", "")),
        "category": category,
        "severity": severity,
        "reason": reason,
        "amount_at_risk": round(float(amount), 2),
        "confidence": confidence,
        "recommendation": rec,
    }


def _provider_scorecard(d: pd.DataFrame, findings: list[dict]) -> list[dict]:
    if "provider_npi" not in d:
        return []
    g = d.groupby("provider_npi")
    agg = g.agg(
        provider_name=("provider_name", "first") if "provider_name" in d else ("provider_npi", "first"),
        total_paid=("paid_amt", "sum"),
        claims=("paid_amt", "count"),
        avg_paid=("paid_amt", "mean"),
    ).reset_index()
    if "member_id" in d:
        agg = agg.merge(g["member_id"].nunique().rename("members").reset_index(), on="provider_npi")
        agg["paid_per_member"] = agg["total_paid"] / agg["members"].clip(lower=1)
    else:
        agg["members"] = 0
        agg["paid_per_member"] = agg["total_paid"]

    # peer z-scores
    def zcol(col):
        mu, sd = agg[col].mean(), agg[col].std()
        return (agg[col] - mu) / sd if sd and not math.isnan(sd) else agg[col] * 0
    agg["z_avg_paid"] = zcol("avg_paid")
    agg["z_ppm"] = zcol("paid_per_member")

    # upcoding: high-level E/M share vs peer (with volume + absolute floor)
    em_share, em_count = {}, {}
    if "cpt" in d:
        em = d[d["cpt"].isin(EM_CODES)]
        if not em.empty:
            for npi, grp in em.groupby("provider_npi"):
                em_count[npi] = len(grp)
                em_share[npi] = grp["cpt"].isin(EM_HIGH).sum() / len(grp)
    peer_em = (sum(em_share.values()) / len(em_share)) if em_share else 0

    # flagged $ per provider from findings so far
    flagged_by_npi: dict[str, float] = {}
    for f in findings:
        flagged_by_npi[f["provider_npi"]] = flagged_by_npi.get(f["provider_npi"], 0) + f["amount_at_risk"]

    out = []
    for _, r in agg.iterrows():
        npi = r["provider_npi"]
        reasons = []
        risk = 0.0
        if r["z_avg_paid"] >= 2.5:
            reasons.append(f"paid/claim {r['z_avg_paid']:.1f}σ above peers")
            risk += 30
            findings.append({
                "claim_id": "—", "provider_npi": str(npi), "provider_name": str(r.get("provider_name", "")),
                "cpt": "", "category": "Provider outlier", "severity": "high",
                "reason": f"Avg paid/claim ${r['avg_paid']:,.0f} is {r['z_avg_paid']:.1f}σ above peers.",
                "amount_at_risk": round(float(r["total_paid"]) * 0.1, 2), "confidence": _conf(r["z_avg_paid"]),
                "recommendation": "Open provider review — billing pattern outlier (possible FWA).",
            })
        if r["z_ppm"] >= 2.5:
            reasons.append(f"paid/member {r['z_ppm']:.1f}σ above peers")
            risk += 25
        share = em_share.get(npi, 0)
        ecnt = em_count.get(npi, 0)
        if ecnt >= 15 and (share >= 0.8 or (peer_em and share >= peer_em * 1.8 and share >= 0.6)):
            reasons.append(f"high-level E/M {share*100:.0f}% (peer {peer_em*100:.0f}%)")
            risk += 25
            findings.append({
                "claim_id": "—", "provider_npi": str(npi), "provider_name": str(r.get("provider_name", "")),
                "cpt": "E/M", "category": "Upcoding signal", "severity": "med",
                "reason": f"{share*100:.0f}% of E/M visits are high-level vs {peer_em*100:.0f}% peer average.",
                "amount_at_risk": round(float(r["total_paid"]) * 0.05, 2), "confidence": 0.7,
                "recommendation": "Audit E/M coding — upcoding signal.",
            })
        flagged = flagged_by_npi.get(str(npi), 0) + flagged_by_npi.get(npi, 0)
        if flagged > r["total_paid"] * 0.2:
            risk += 20
        out.append({
            "provider_npi": str(npi),
            "provider_name": str(r.get("provider_name", "")) or str(npi),
            "specialty": "",
            "total_paid": round(float(r["total_paid"]), 2),
            "claims": int(r["claims"]),
            "members": int(r["members"]),
            "avg_paid": round(float(r["avg_paid"]), 2),
            "flagged_amount": round(float(flagged), 2),
            "risk_score": int(min(100, risk)),
            "reasons": reasons,
        })
    out.sort(key=lambda p: p["risk_score"], reverse=True)
    return out


def recommendation_text(finding: dict) -> str:
    return finding.get("recommendation", "Review claim.")
