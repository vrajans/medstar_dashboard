"use client";

import { useEffect, useState } from "react";
import { api, auth, fmtCurrency, type Overview, type SeriesPoint, type Entity } from "@/lib/api";
import { useDomain } from "@/lib/domains";
import KpiCard from "@/components/KpiCard";
import Section from "@/components/Section";
import LineTrend from "@/components/LineTrend";
import BarChartH from "@/components/BarChartH";

export default function PurchasesPage() {
  const [kpi, setKpi] = useState<Overview | null>(null);
  const [series, setSeries] = useState<SeriesPoint[]>([]);
  const [suppliers, setSuppliers] = useState<Entity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const d = useDomain();

  useEffect(() => {
    const t = auth.tenantId;
    Promise.all([api.overview(t), api.timeseries(t), api.suppliers(t, 10)])
      .then(([o, ts, sp]) => {
        setKpi(o); setSeries(ts.series); setSuppliers(sp.suppliers);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-slate-400">Loading…</div>;
  if (error) return <div className="rounded-lg bg-red-50 p-4 text-danger">{error}</div>;

  const totalSpend = kpi?.costs ?? 0;
  const avgSupplier = suppliers.length ? totalSpend / suppliers.length : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-navy">{d.costWord}</h1>
        <p className="text-sm text-slate-500">
          Spend, trend, and top {d.suppliersWord.toLowerCase()}.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label={`Total ${d.costWord}`} value={fmtCurrency(totalSpend)} accent="#D97706" />
        <KpiCard label={d.suppliersWord} value={`${suppliers.length}`} accent="#0EA5E9" />
        <KpiCard label={`Avg / ${d.suppliersWord.replace(/s$/, "")}`} value={fmtCurrency(avgSupplier)} accent="#7C3AED" />
        <KpiCard
          label="Net Cash Flow"
          value={fmtCurrency(kpi?.net_cash_flow ?? 0)}
          accent={(kpi?.net_cash_flow ?? 0) >= 0 ? "#059669" : "#DC2626"}
        />
      </div>

      <Section title={`Monthly ${d.costWord.toLowerCase()} trend`}>
        {series.length ? <LineTrend data={series} dataKey="cost" color="#D97706" area currency />
          : <div className="py-12 text-center text-sm text-slate-400">No dated cost data.</div>}
      </Section>

      <Section title={`Top ${d.suppliersWord.toLowerCase()} by spend`}>
        {suppliers.length ? <BarChartH data={suppliers} color="#D97706" />
          : <div className="py-12 text-center text-sm text-slate-400">No {d.suppliersWord.toLowerCase()} data for this dataset.</div>}
      </Section>
    </div>
  );
}
