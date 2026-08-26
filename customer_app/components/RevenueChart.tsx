"use client";

import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from "recharts";
import type { SeriesPoint } from "@/lib/api";

export default function RevenueChart({ data }: { data: SeriesPoint[] }) {
  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
          <XAxis dataKey="period" tick={{ fontSize: 11, fill: "#64748B" }} />
          <YAxis
            tick={{ fontSize: 11, fill: "#64748B" }}
            tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}K` : `${v}`)}
          />
          <Tooltip
            formatter={(v: number) => `$${Number(v).toLocaleString()}`}
            contentStyle={{ borderRadius: 8, border: "1px solid #E2E8F0", fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="revenue" name="Revenue" fill="#059669" radius={[3, 3, 0, 0]} />
          <Bar dataKey="cost" name="Cost" fill="#D97706" radius={[3, 3, 0, 0]} />
          <Line dataKey="net" name="Net" stroke="#2563EB" strokeWidth={2} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
