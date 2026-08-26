"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, auth, fmtCurrency, type Overview, type SeriesPoint, type Entity, type PIResult } from "@/lib/api";
import { useDomain } from "@/lib/domains";
import KpiCard from "@/components/KpiCard";
import Section from "@/components/Section";
import RevenueChart from "@/components/RevenueChart";
import SimpleChart from "@/components/SimpleChart";

export default function OverviewPage() {
  const d = useDomain();

  // sales-style state
  const [kpi, setKpi] = useState<Overview | null>(null);
  const [series, setSeries] = useState<SeriesPoint[]>([]);
  const [entities, setEntities] = useState<Entity[]>([]);
  // healthcare (PI) state
  const [pi, setPi] = useState<PIResult | null>(null);
  const [piName, setPiName] = useState<string>("");
  const [hc, setHc] = useState<boolean>(false);

  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const t = auth.tenantId;
      // decide from the tenant's actual domain (single source of truth)
      let healthcare = false;
      try { const mt = await api.myTenant(); healthcare = mt?.domain_type === "healthcare"; }
      catch { /* fall back to sales view */ }
      setHc(healthcare);
      try {
        if (healthcare) {
          const runs = await api.piListRuns(t);
          if (runs.length) { const r = await api.piGetRun(runs[0].id); setPi(r.result); setPiName(r.name); }
        } else {
          const [o, ts, te] = await Promise.all([api.overview(t), api.timeseries(t), api.topEntities(t, 8)]);
          setKpi(o); setSeries(ts.series); setEntities(te.entities);
        }
      } catch (e: any) { setError(e.message); }
      finally { setLoading(false); }
    })();
  }, []);

  if (loading) return <div className="text-slate-400">Loading…</div>;
  if (error) return <div className="rounded-lg bg-red-50 p-4 text-danger">{error}</div>;

  // ── Healthcare / Payment-Integrity overview ──
  if (hc) {
    const s = pi?.summary;
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-navy">Overview</h1>
          <p className="text-sm text-slate-500">Payment integrity summary — from your latest analysis.</p>
        </div>
        {!s ? (
          <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
            No analysis yet. Upload a claims file on the{" "}
            <Link href="/pi" className="font-semibold text-brand">Payment Integrity</Link> page to get started.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <KpiCard label="Claims Scanned" value={s.total_claims.toLocaleString()} accent="#2563EB" />
              <KpiCard label="Total Paid" value={fmtCurrency(s.total_paid)} accent="#0EA5E9" />
              <KpiCard label="At Risk" value={fmtCurrency(s.amount_at_risk)} accent="#DC2626" sub={`${s.pct_at_risk}% of paid`} />
              <KpiCard label="Providers Flagged" value={`${s.providers_flagged}`} accent="#D97706" sub={`${s.flagged_claims} claims`} />
            </div>
            <Section title="Recoverable dollars by category" right={<span className="text-xs text-slate-400">{piName}</span>}>
              <SimpleChart data={s.by_category.map((c) => ({ name: c.category, value: Math.round(c.amount) }))}
                           horizontal color="#DC2626" height={Math.max(180, s.by_category.length * 32)} />
            </Section>
            <Section title="Highest-risk providers">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-slate-400">
                    <th className="pb-2">Provider</th><th className="pb-2 text-right">Risk</th>
                    <th className="pb-2 text-right">Paid</th><th className="pb-2 text-right">Flagged $</th>
                  </tr>
                </thead>
                <tbody>
                  {pi!.providers.slice(0, 6).map((p) => (
                    <tr key={p.provider_npi} className="border-t border-slate-100">
                      <td className="py-2 text-navy">{p.provider_name}</td>
                      <td className="py-2 text-right font-bold tabular-nums">{p.risk_score}</td>
                      <td className="py-2 text-right tabular-nums text-slate-500">{fmtCurrency(p.total_paid)}</td>
                      <td className="py-2 text-right tabular-nums text-danger">{fmtCurrency(p.flagged_amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-3 text-sm">
                <Link href="/pi" className="font-semibold text-brand">Open full Payment Integrity worklist →</Link>
              </div>
            </Section>
          </>
        )}
      </div>
    );
  }

  // ── Default (sales/revenue) overview ──
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-navy">Overview</h1>
        <p className="text-sm text-slate-500">Live from your dimensional warehouse.</p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label={d.kpiRevenue} value={fmtCurrency(kpi?.revenue ?? 0)} accent="#059669" />
        <KpiCard label={d.kpiCost} value={fmtCurrency(kpi?.costs ?? 0)} accent="#D97706" />
        <KpiCard label="Net Cash Flow" value={fmtCurrency(kpi?.net_cash_flow ?? 0)}
                 accent={(kpi?.net_cash_flow ?? 0) >= 0 ? "#059669" : "#DC2626"} />
        <KpiCard label="Gross Margin" value={`${kpi?.gross_margin_pct ?? 0}%`} accent="#2563EB" />
        <KpiCard label={d.kpiTxns} value={`${(kpi?.transactions ?? 0).toLocaleString()}`} accent="#0EA5E9" />
        <KpiCard label={d.kpiCustomers} value={`${(kpi?.customers ?? 0).toLocaleString()}`} accent="#7C3AED" />
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="mb-3 text-sm font-semibold text-navy">{d.kpiRevenue} vs {d.kpiCost} by month</div>
        {series.length ? <RevenueChart data={series} />
          : <div className="py-12 text-center text-sm text-slate-400">No dated data yet.</div>}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="mb-3 text-sm font-semibold text-navy">Top {d.customersWord.toLowerCase()}</div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-400">
              <th className="pb-2">{d.customersWord}</th>
              <th className="pb-2 text-right">{d.kpiRevenue}</th>
              <th className="pb-2 text-right">{d.txnWord}</th>
            </tr>
          </thead>
          <tbody>
            {entities.map((e) => (
              <tr key={e.name} className="border-t border-slate-100">
                <td className="py-2 text-navy">{e.name}</td>
                <td className="py-2 text-right tabular-nums">{fmtCurrency(e.total)}</td>
                <td className="py-2 text-right tabular-nums text-slate-500">{e.transactions}</td>
              </tr>
            ))}
            {!entities.length && (
              <tr><td colSpan={3} className="py-6 text-center text-slate-400">No client data yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
