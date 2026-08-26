"use client";

import { useEffect, useState } from "react";
import { api, auth, fmtCurrency, type Overview, type SeriesPoint, type Entity } from "@/lib/api";
import { useDomain } from "@/lib/domains";
import KpiCard from "@/components/KpiCard";
import Section from "@/components/Section";
import RevenueChart from "@/components/RevenueChart";
import LineTrend from "@/components/LineTrend";
import BarChartH from "@/components/BarChartH";

export default function SalesPage() {
  const [kpi, setKpi] = useState<Overview | null>(null);
  const [series, setSeries] = useState<SeriesPoint[]>([]);
  const [clients, setClients] = useState<Entity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const d = useDomain();

  useEffect(() => {
    const t = auth.tenantId;
    Promise.all([api.overview(t), api.timeseries(t), api.topEntities(t, 10)])
      .then(([o, ts, te]) => {
        setKpi(o); setSeries(ts.series); setClients(te.entities);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-slate-400">Loading…</div>;
  if (error) return <div className="rounded-lg bg-red-50 p-4 text-danger">{error}</div>;

  const hasMargin = series.some((s) => (s.margin ?? 0) > 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-navy">{d.revenueWord}</h1>
        <p className="text-sm text-slate-500">
          {d.revenueWord} performance, trend, and top {d.customersWord.toLowerCase()}.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label={`Total ${d.revenueWord}`} value={fmtCurrency(kpi?.revenue ?? 0)} accent="#059669" />
        <KpiCard label="Avg Margin" value={`${kpi?.avg_margin_pct ?? 0}%`} accent="#2563EB" />
        <KpiCard label={d.kpiTxns} value={`${(kpi?.transactions ?? 0).toLocaleString()}`} accent="#0EA5E9" />
        <KpiCard label={d.kpiCustomers} value={`${(kpi?.customers ?? 0).toLocaleString()}`} accent="#7C3AED" />
      </div>

      <Section title={`${d.revenueWord} vs ${d.costWord} by month`}>
        {series.length ? <RevenueChart data={series} />
          : <div className="py-12 text-center text-sm text-slate-400">No dated data.</div>}
      </Section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Section title={`Cumulative ${d.revenueWord.toLowerCase()}`}>
          {series.length ? <LineTrend data={series} dataKey="cumulative" color="#2563EB" area currency />
            : <div className="py-12 text-center text-sm text-slate-400">No data.</div>}
        </Section>
        <Section title="Margin % trend">
          {hasMargin ? <LineTrend data={series} dataKey="margin" color="#D97706" suffix="%" />
            : <div className="py-12 text-center text-sm text-slate-400">No margin data for this dataset.</div>}
        </Section>
      </div>

      <Section title={`Top ${d.customersWord.toLowerCase()} by ${d.revenueWord.toLowerCase()}`}>
        {clients.length ? <BarChartH data={clients} color="#2563EB" />
          : <div className="py-12 text-center text-sm text-slate-400">No {d.customersWord.toLowerCase()} breakdown for this dataset.</div>}
      </Section>
    </div>
  );
}
