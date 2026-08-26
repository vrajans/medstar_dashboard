export default function KpiCard({
  label,
  value,
  accent = "#2563EB",
  sub,
}: {
  label: string;
  value: string;
  accent?: string;
  sub?: string;
}) {
  return (
    <div
      className="rounded-xl border border-slate-200 bg-white p-5"
      style={{ borderTop: `3px solid ${accent}` }}
    >
      <div className="text-2xl font-bold tracking-tight text-navy tabular-nums">{value}</div>
      <div className="mt-1 text-[0.7rem] font-medium uppercase tracking-wider text-slate-500">
        {label}
      </div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}
