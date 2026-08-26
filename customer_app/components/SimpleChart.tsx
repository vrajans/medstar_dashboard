"use client";

import {
  ResponsiveContainer, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";

type Pt = { name: string; value: number };

export default function SimpleChart({
  data, kind = "bar", color = "#2563EB", height = 260, horizontal = false,
}: {
  data: Pt[]; kind?: "bar" | "line"; color?: string; height?: number; horizontal?: boolean;
}) {
  const rows = data.map((d) => ({
    name: d.name.length > 22 ? d.name.slice(0, 22) + "…" : d.name,
    value: d.value,
  }));
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        {kind === "line" ? (
          <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
            <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#64748B" }} />
            <YAxis tick={{ fontSize: 10, fill: "#64748B" }} />
            <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid #E2E8F0", fontSize: 12 }} />
            <Line dataKey="value" stroke={color} strokeWidth={2} dot={false} />
          </LineChart>
        ) : horizontal ? (
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 20, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 10, fill: "#64748B" }} />
            <YAxis type="category" dataKey="name" width={130} tick={{ fontSize: 10, fill: "#334155" }} />
            <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid #E2E8F0", fontSize: 12 }} />
            <Bar dataKey="value" fill={color} radius={[0, 3, 3, 0]} />
          </BarChart>
        ) : (
          <BarChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
            <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#64748B" }} />
            <YAxis tick={{ fontSize: 10, fill: "#64748B" }} />
            <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid #E2E8F0", fontSize: 12 }} />
            <Bar dataKey="value" fill={color} radius={[3, 3, 0, 0]} />
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
