"""
generate_claims.py — realistic synthetic US health-insurance claims for demos.

Produces:
  demo_claims.csv             — the file you upload in the demo
  demo_claims_answer_key.csv  — the problems we planted (for the presenter)

It plants a controlled number of issues across EVERY detection category so the
demo is predictable: you know exactly what should light up.

Usage:
  python generate_claims.py            # ~6000 claims
  python generate_claims.py 12000      # custom size
"""
import random, sys
import pandas as pd

random.seed(42)

# ── realistic catalogs ───────────────────────────────────────────────────────
# code: (typical paid, typical units, category)
CPT = {
    "99212": (52, 1, "E/M"), "99213": (78, 1, "E/M"), "99214": (115, 1, "E/M"),
    "99204": (172, 1, "E/M"), "99205": (225, 1, "E/M"), "99215": (185, 1, "E/M"),
    "80053": (15, 1, "Lab"), "85025": (11, 1, "Lab"), "80061": (19, 1, "Lab"),
    "36415": (3, 1, "Lab"), "83036": (14, 1, "Lab"),
    "70450": (232, 1, "Imaging"), "71046": (48, 1, "Imaging"),
    "72148": (455, 1, "Imaging"), "93000": (20, 1, "Imaging"), "76700": (120, 1, "Imaging"),
    "20610": (66, 1, "Procedure"), "12001": (128, 1, "Procedure"),
    "29881": (1180, 1, "Procedure"), "45378": (640, 1, "Procedure"),
    "99385": (150, 1, "Preventive"), "G0439": (140, 1, "Preventive"),
}
EM_CODES = ["99212", "99213", "99214", "99204", "99205", "99215"]
EM_HIGH = ["99204", "99205", "99214", "99215"]
# realistic billing frequency (level 3/4 dominate; level 5 rare; labs common)
CPT_WEIGHTS = {
    "99213": 20, "99214": 12, "99212": 7, "99204": 3, "99215": 2, "99205": 1,
    "80053": 10, "85025": 8, "80061": 5, "36415": 14, "83036": 5,
    "70450": 3, "71046": 5, "72148": 2, "93000": 4, "76700": 3,
    "20610": 3, "12001": 2, "29881": 1, "45378": 1, "99385": 3, "G0439": 2,
}
_CODES = list(CPT_WEIGHTS.keys())
_WEIGHTS = list(CPT_WEIGHTS.values())
ICD = ["E11.9", "I10", "M54.5", "J06.9", "Z00.00", "E78.5", "K21.9", "M17.11",
       "R51.9", "N39.0", "J45.909", "F41.1"]
SPECIALTIES = ["Family Medicine", "Internal Medicine", "Orthopedics",
               "Cardiology", "Radiology", "Gastroenterology"]
POS = ["11", "22", "19", "23"]  # office, on-campus outpatient, off-campus, ER


def make_provider(i):
    return {
        "npi": f"1{random.randint(100000000, 999999999)}",
        "name": f"Dr {random.choice(['Smith','Patel','Nguyen','Garcia','Kim','Brown','Lee','Cohen','Rao','Davis'])} {i}",
        "specialty": random.choice(SPECIALTIES),
    }


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    providers = [make_provider(i) for i in range(1, 41)]
    members = [{"id": f"MBR{1000+i}", "age": random.randint(1, 92),
                "gender": random.choice(["M", "F"])} for i in range(2000)]

    rows, answer = [], []
    cid = 500000

    def new_claim(prov, mem, code, paid=None, units=None, dos=None):
        nonlocal cid
        base, u0, _ = CPT[code]
        cid += 1
        r = {
            "claim_id": f"CLM{cid}", "line_no": 1, "member_id": mem["id"],
            "member_age": mem["age"], "member_gender": mem["gender"],
            "provider_npi": prov["npi"], "provider_name": prov["name"],
            "specialty": prov["specialty"],
            "service_date": dos or f"2025-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "place_of_service": random.choice(POS), "cpt": code,
            "modifier": random.choice(["", "", "", "25", "59"]),
            "icd10": random.choice(ICD), "units": units or u0,
        }
        p = paid if paid is not None else round(base * random.uniform(0.85, 1.18), 2)
        r["billed_amt"] = round(p * random.uniform(1.4, 2.2), 2)
        r["allowed_amt"] = round(p * random.uniform(1.0, 1.1), 2)
        r["paid_amt"] = p
        return r

    # ── baseline: clean claims (realistic code frequency) ────────────────────
    for _ in range(n):
        code = random.choices(_CODES, weights=_WEIGHTS, k=1)[0]
        rows.append(new_claim(random.choice(providers), random.choice(members), code))

    # ── plant issues ─────────────────────────────────────────────────────────
    def plant(kind, r, note):
        answer.append({"issue": kind, "claim_id": r["claim_id"],
                       "provider_npi": r["provider_npi"], "note": note})
        rows.append(r)

    # 1) duplicates (25)
    for _ in range(25):
        src = random.choice(rows[:n])
        dup = dict(src); cid += 1; dup["claim_id"] = f"CLM{cid}"
        answer.append({"issue": "Duplicate", "claim_id": dup["claim_id"],
                       "provider_npi": dup["provider_npi"], "source_id": src["claim_id"],
                       "note": f"duplicate of {src['claim_id']}"})
        rows.append(dup)

    # 2) high-$ outliers (20) — pay 8–14× the norm
    for _ in range(20):
        code = random.choice(["80053", "36415", "93000", "71046"])
        r = new_claim(random.choice(providers), random.choice(members), code,
                      paid=round(CPT[code][0] * random.uniform(8, 14), 2))
        plant("High-$ outlier", r, f"{code} paid ~{r['paid_amt']:.0f} vs ~{CPT[code][0]}")

    # 3) unit outliers (15)
    for _ in range(15):
        code = random.choice(["36415", "80053", "20610"])
        r = new_claim(random.choice(providers), random.choice(members), code,
                      units=random.randint(60, 200))
        plant("Unit outlier", r, f"{r['units']} units of {code}")

    # 4) gender edits (10)
    for _ in range(10):
        male = {"id": f"MBR{random.randint(1,1999)}", "age": random.randint(20, 60), "gender": "M"}
        r = new_claim(random.choice(providers), male, "99214")
        r["cpt"] = "59400"; r["paid_amt"] = 2800; r["billed_amt"] = 4200; r["allowed_amt"] = 3000
        plant("Gender edit", r, "male member billed obstetric 59400")

    # 5) upcoding providers (3) — nearly all high-level E/M at inflated pay
    for k in range(3):
        up = make_provider(90 + k); up["name"] = f"Dr Upcoder {k+1}"
        for _ in range(60):
            code = random.choice(EM_HIGH)
            r = new_claim(up, random.choice(members), code,
                          paid=round(CPT[code][0] * random.uniform(1.1, 1.3), 2))
            rows.append(r)
        answer.append({"issue": "Upcoding", "claim_id": "—", "provider_npi": up["npi"],
                       "note": f"{up['name']} bills ~100% high-level E/M"})

    # 6) provider $ outliers (2) — very high avg paid/claim
    for k in range(2):
        bad = make_provider(80 + k); bad["name"] = f"Dr HighBiller {k+1}"
        for _ in range(50):
            code = random.choice(["29881", "45378", "72148"])
            r = new_claim(bad, random.choice(members), code,
                          paid=round(CPT[code][0] * random.uniform(1.4, 1.9), 2))
            rows.append(r)
        answer.append({"issue": "Provider outlier", "claim_id": "—", "provider_npi": bad["npi"],
                       "note": f"{bad['name']} avg paid/claim far above peers"})

    df = pd.DataFrame(rows).sample(frac=1, random_state=1).reset_index(drop=True)
    df.to_csv("demo_claims.csv", index=False)
    pd.DataFrame(answer).to_csv("demo_claims_answer_key.csv", index=False)

    print(f"Wrote demo_claims.csv          ({len(df):,} claims, ${df['paid_amt'].sum():,.0f} paid)")
    print(f"Wrote demo_claims_answer_key.csv ({len(answer)} planted issues)")

    # ── demo-readiness: run the engine, report catch rate ────────────────────
    import payment_integrity as pi
    res = pi.analyze_claims(df)
    s = res["summary"]
    flagged_ids = {f["claim_id"] for f in res["findings"]}
    flagged_npis = {f["provider_npi"] for f in res["findings"]}
    print("\n── DETECTION REPORT ──")
    print(f"  Flagged {s['flagged_claims']} claims · ${s['amount_at_risk']:,.0f} at risk "
          f"({s['pct_at_risk']}% of paid) · {s['providers_flagged']} providers")
    by = {}
    for a in answer:
        if a["issue"] == "Duplicate":
            caught = a["claim_id"] in flagged_ids or a.get("source_id") in flagged_ids
        elif a["claim_id"] != "—":
            caught = a["claim_id"] in flagged_ids
        else:
            caught = a["provider_npi"] in flagged_npis
        d = by.setdefault(a["issue"], [0, 0]); d[1] += 1; d[0] += 1 if caught else 0
    for k, (c, t) in sorted(by.items()):
        print(f"  {k:16} caught {c}/{t}")
    print("\nDemo tip: keep demo_claims_answer_key.csv open so you can point to each planted issue.")


if __name__ == "__main__":
    main()
