"""
test_payment_integrity.py — proves the PI engine catches injected problems.
Run: python test_payment_integrity.py
Also writes sample_claims.csv (a demo file with seeded issues).
"""
import random, warnings
warnings.filterwarnings("ignore")
import pandas as pd
import payment_integrity as pi

random.seed(7)

CPTS = {  # code: typical paid
    "99213": 90, "99214": 130, "99204": 200, "70450": 320, "80053": 45,
    "93000": 55, "36415": 12, "71046": 60,
}
FAILS = []
def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond: FAILS.append(name)

def gen():
    rows = []
    cid = 1000
    providers = [(f"100000000{i}", f"Dr Provider {i}", "M" if i % 2 else "F") for i in range(1, 11)]
    for _ in range(600):
        npi, pname, _ = random.choice(providers)
        cpt = random.choice(list(CPTS))
        base = CPTS[cpt]
        paid = round(base * random.uniform(0.85, 1.15), 2)
        mem = f"M{random.randint(1, 120):04d}"
        rows.append(dict(
            claim_id=f"CLM{cid}", member_id=mem, member_age=random.randint(1, 90),
            member_gender=random.choice(["M", "F"]), provider_npi=npi, provider_name=pname,
            service_date=f"2025-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            place_of_service="11", cpt=cpt, units=1, billed_amt=round(paid*1.6, 2),
            allowed_amt=round(paid*1.05, 2), paid_amt=paid,
        )); cid += 1

    # ── inject known problems ──
    # duplicate of the first claim
    dup = dict(rows[0]); dup["claim_id"] = "CLM_DUP"
    rows.append(dup)
    # high-$ outlier for 80053 (normally ~45, pay 900)
    rows.append(dict(claim_id="CLM_HIDOLLAR", member_id="M0007", member_age=50,
        member_gender="F", provider_npi="1000000005", provider_name="Dr Provider 5",
        service_date="2025-06-01", place_of_service="11", cpt="80053", units=1,
        billed_amt=1500, allowed_amt=950, paid_amt=900))
    # unit outlier
    rows.append(dict(claim_id="CLM_UNITS", member_id="M0008", member_age=40,
        member_gender="M", provider_npi="1000000006", provider_name="Dr Provider 6",
        service_date="2025-06-02", place_of_service="11", cpt="36415", units=80,
        billed_amt=200, allowed_amt=120, paid_amt=100))
    # gender edit — male member, obstetric code 59400
    rows.append(dict(claim_id="CLM_GENDER", member_id="M0009", member_age=35,
        member_gender="M", provider_npi="1000000007", provider_name="Dr Provider 7",
        service_date="2025-06-03", place_of_service="11", cpt="59400", units=1,
        billed_amt=4000, allowed_amt=3000, paid_amt=2800))
    # upcoding + high-paying provider: add 40 high-level E/M for one NPI at inflated pay
    for i in range(40):
        rows.append(dict(claim_id=f"CLM_UP{i}", member_id=f"M{random.randint(1,120):04d}",
            member_age=60, member_gender="F", provider_npi="9999999999",
            provider_name="Dr Upcoder", service_date="2025-07-15", place_of_service="11",
            cpt=random.choice(["99204", "99215"]), units=1, billed_amt=400,
            allowed_amt=320, paid_amt=round(random.uniform(260, 320), 2)))
    return pd.DataFrame(rows)

def main():
    df = gen()
    df.to_csv("sample_claims.csv", index=False)
    print(f"Generated {len(df)} claims → sample_claims.csv")
    res = pi.analyze_claims(df)
    cats = {f["category"] for f in res["findings"]}
    ids = {f["claim_id"] for f in res["findings"]}
    print("Summary:", {k: res["summary"][k] for k in ("total_claims", "flagged_claims", "amount_at_risk", "pct_at_risk")})
    print("Categories found:", sorted(cats))

    check("duplicate flagged", "CLM_DUP" in ids and "Duplicate" in cats)
    check("high-$ outlier flagged", "CLM_HIDOLLAR" in ids and "High-$ outlier" in cats)
    check("unit outlier flagged", "CLM_UNITS" in ids and "Unit outlier" in cats)
    check("gender edit flagged", "CLM_GENDER" in ids and "Gender edit" in cats)
    check("provider outlier or upcoding flagged", ("Provider outlier" in cats) or ("Upcoding signal" in cats))
    up = [p for p in res["providers"] if p["provider_npi"] == "9999999999"]
    check("upcoder provider has high risk score", bool(up) and up[0]["risk_score"] >= 40)
    check("findings are prioritized (desc by $×conf)",
          all(res["findings"][i]["amount_at_risk"] * res["findings"][i]["confidence"] >=
              res["findings"][i+1]["amount_at_risk"] * res["findings"][i+1]["confidence"]
              for i in range(min(20, len(res["findings"]) - 1))))
    check("amount at risk > 0", res["summary"]["amount_at_risk"] > 0)

    print(f"\n{'ALL PASS ✅' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    return 0 if not FAILS else 1

if __name__ == "__main__":
    raise SystemExit(main())
