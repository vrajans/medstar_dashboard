export default function Section({
  title, children, right,
}: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold text-navy">{title}</div>
        {right}
      </div>
      {children}
    </div>
  );
}
