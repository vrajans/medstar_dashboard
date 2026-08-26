"use client";

import { useEffect, useState } from "react";
import Papa from "papaparse";
import { api, auth, fmtCurrency, type PIResult, type PIRunMeta } from "@/lib/api";
import KpiCard from "@/components/KpiCard";
import Section from "@/components/Section";
import SimpleChart from "@/components/SimpleChart";

const SEV: Record<string, string> = {
  high: "bg-red-50 text-red-700", med: "bg-amber-50 text-amber-700", low: "bg-slate-100 text-slate-600",
};

export default function PaymentIntegrityPage() {
  const [res, setRes] = useState<PIResult | null>(null);
  const [runs, setRuns] = useState<PIRunMeta[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [narrative, setNarrative] = useState<string | null>(null);
  const [tab, setTab] = useState<"claims" | "providers">("claims");

  async function refreshRuns() {
    try { setRuns(await api.piListRuns(auth.tenantId)); } catch { /* ignore */ }
  }
  // On load: list saved runs AND auto-open the most recent so a refresh keeps
  // the analysis on screen (the run persists in the warehouse).
  useEffect(() => {
    (async () => {
      try {
        const list = await api.piListRuns(auth.tenantId);
        setRuns(list);
        if (list.length) openRun(list[0].id);
      } catch { /* ignore */ }
    })();
  }, []);

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null); setNarrative(null); setRes(null); setFileName(file.name); setBusy(true);
    const name = file.name.replace(/\.[^.]+$/, "");
    Papa.parse<any>(file, {
      header: true, dynamicTyping: true, skipEmptyLines: true,
      complete: async (parsed) => {
        try {
          // ingest → warehouse → analyze → SAVE (persists across sessions)
          const r = await api.piCreateRun(name, auth.tenantId, parsed.data);
          setRes(r.result); setOpenId(r.id);
          await refreshRuns();
        } catch (err: any) { setError(err.message); }
        finally { setBusy(false); }
      },
      error: (err) => { setError(err.message); setBusy(false); },
    });
  }

  async function openRun(id: number) {
    setBusy(true); setError(null); setNarrative(null);
    try { const r = await api.piGetRun(id); setRes(r.result); setOpenId(id); setFileName(r.name); }
    catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function removeRun(id: number) {
    try { await api.piDeleteRun(id); if (openId === id) { setRes(null); setOpenId(null); } await refreshRuns(); }
    catch (e: any) { setError(e.message); }
  }

  async function getNarrative() {
    if (!res) return;
    setBusy(true);
    try { setNarrative((await api.piNarrative(res.summary, res.findings.slice(0, 5))).narrative); }
    catch (e: any) { setNarrative("⚠️ " + e.message); }
    finally { setBusy(false); }
  }

  const s = res?.summary;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-navy">Payment Integrity</h1>
        <p className="text-sm text-slate-500">
          Upload a claims file — it lands in your warehouse, we flag likely improper payments,
          and the run is saved so it's here next time you log in.
        </p>
      </div>

      {/* Saved runs (persisted in the warehouse) */}
      <Section title="Saved analyses">
        {runs.length === 0 ? (
          <div className="py-3 text-sm text-slate-400">No saved runs yet — upload a claims file below.</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {runs.map((r) => (
              <div key={r.id} className="flex items-center justify-between py-2">
                <button onClick={() => openRun(r.id)}
                        className={`text-left text-sm font-medium ${openId === r.id ? "text-brand" : "text-navy hover:text-brand"}`}>
                  {r.name}
                  <span className="ml-2 text-xs font-normal text-slate-400">
                    {r.row_count.toLocaleString()} claims · {fmtCurrency(r.amount_at_risk)} at risk
                    ({r.pct_at_risk}%) · {r.created_at}
                  </span>
                </button>
                <button onClick={() => removeRun(r.id)} className="text-xs text-slate-400 hover:text-danger">Delete</button>
              </div>
            ))}
          </div>
        )}
      </Section>

      <div className="rounded-xl border-2 border-dashed border-slate-300 bg-white p-6 text-center">
        <input id="pi-file" type="file" accept=".csv" onChange={handleFile} className="hidden" />
        <label htmlFor="pi-file"
               className="cursor-pointer rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700">
          Upload claims (CSV)
        </label>
        <div className="mt-2 text-xs text-slate-400">
          {busy ? "Ingesting → warehouse → analyzing…" : fileName ? `Loaded: ${fileName}` : "Try demo_claims.csv."}
        </div>
        {error && <div className="mt-3 text-sm text-danger">{error}</div>}
      </div>

      {s && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KpiCard label="Claims Scanned" value={s.total_claims.toLocaleString()} accent="#2563EB" />
            <KpiCard label="Total Paid" value={fmtCurrency(s.total_paid)} accent="#0EA5E9" />
            <KpiCard label="At Risk" value={fmtCurrency(s.amount_at_risk)} accent="#DC2626"
                     sub={`${s.pct_at_risk}% of paid`} />
            <KpiCard label="Flagged Claims" value={`${s.flagged_claims}`} accent="#D97706"
                     sub={`${s.providers_flagged} providers`} />
          </div>

          {narrative ? (
            <div className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm text-navy whitespace-pre-wrap">
              {narrative}
            </div>
          ) : (
            <button onClick={getNarrative} disabled={busy}
                    className="rounded-lg border border-brand px-3 py-1.5 text-sm font-semibold text-brand hover:bg-blue-50 disabled:opacity-60">
              ✨ AI executive summary
            </button>
          )}

          <Section title="Recoverable dollars by category">
            <SimpleChart
              data={s.by_category.map((c) => ({ name: c.category, value: Math.round(c.amount) }))}
              horizontal color="#DC2626" height={Math.max(200, s.by_category.length * 34)} />
          </Section>

          {/* tabs */}
          <div className="flex gap-2">
            {(["claims", "providers"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)}
                      className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
                        tab === t ? "bg-navy text-white" : "bg-white text-slate-500 border border-slate-200"}`}>
                {t === "claims" ? "Flagged claims" : "Provider scorecard"}
              </button>
            ))}
          </div>

          {tab === "claims" ? (
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400">
                    <th className="p-3">Claim</th><th className="p-3">Category</th><th className="p-3">Reason</th>
                    <th className="p-3 text-right">At risk</th><th className="p-3 text-right">Conf.</th>
                    <th className="p-3">Recommendation</th>
                  </tr>
                </thead>
                <tbody>
                  {res!.findings.slice(0, 100).map((f, i) => (
                    <tr key={i} className="border-b border-slate-50 align-top">
                      <td className="p-3 font-medium text-navy">{f.claim_id}<div className="text-xs text-slate-400">{f.cpt}</div></td>
                      <td className="p-3"><span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEV[f.severity]}`}>{f.category}</span></td>
                      <td className="p-3 text-slate-600">{f.reason}</td>
                      <td className="p-3 text-right font-semibold tabular-nums text-danger">{fmtCurrency(f.amount_at_risk)}</td>
                      <td className="p-3 text-right tabular-nums text-slate-500">{Math.round(f.confidence * 100)}%</td>
                      <td className="p-3 text-slate-600">{f.recommendation}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400">
                    <th className="p-3">Provider</th><th className="p-3 text-right">Risk</th>
                    <th className="p-3 text-right">Paid</th><th className="p-3 text-right">Claims</th>
                    <th className="p-3 text-right">Flagged $</th><th className="p-3">Why</th>
                  </tr>
                </thead>
                <tbody>
                  {res!.providers.slice(0, 50).map((p) => (
                    <tr key={p.provider_npi} className="border-b border-slate-50 align-top">
                      <td className="p-3 font-medium text-navy">{p.provider_name}<div className="text-xs text-slate-400">{p.provider_npi}</div></td>
                      <td className="p-3 text-right">
                        <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${p.risk_score >= 60 ? "bg-red-50 text-red-700" : p.risk_score >= 30 ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-500"}`}>
                          {p.risk_score}
                        </span>
                      </td>
                      <td className="p-3 text-right tabular-nums text-slate-600">{fmtCurrency(p.total_paid)}</td>
                      <td className="p-3 text-right tabular-nums text-slate-500">{p.claims}</td>
                      <td className="p-3 text-right tabular-nums text-danger">{fmtCurrency(p.flagged_amount)}</td>
                      <td className="p-3 text-xs text-slate-500">{p.reasons.join("; ") || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
