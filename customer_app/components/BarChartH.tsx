"use client";

import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";
import { fmtCurrency, type Entity } from "@/lib/api";

export default function BarChartH({
  data, color = "#2563EB",
}: { data: Entity[]; color?: string }) {
  const rows = data.map((d) => ({
    name: d.name.length > 24 ? d.name.slice(0, 24) + "…" : d.name,
    total: d.total,
  }));
  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ left: 10, right: 24, top: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 11, fill: "#64748B" }}
                 tickFormatter={(v) => fmtCurrency(v)} />
          <YAxis type="category" dataKey="name" width={150}
                 tick={{ fontSize: 11, fill: "#334155" }} />
          <Tooltip formatter={(v: number) => fmtCurrency(v)}
                   contentStyle={{ borderRadius: 8, border: "1px solid #E2E8F0", fontSize: 12 }} />
          <Bar dataKey="total" fill={color} radius={[0, 3, 3, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
