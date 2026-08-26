"use client";

import {
  ResponsiveContainer, ComposedChart, Area, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";

/** Generic single-metric trend. Set `area` for a filled area, `suffix` for units (e.g. "%"). */
export default function LineTrend({
  data, dataKey, color = "#0D9488", suffix = "", area = false, currency = false,
}: {
  data: any[]; dataKey: string; color?: string; suffix?: string;
  area?: boolean; currency?: boolean;
}) {
  const fmt = (v: number) =>
    currency
      ? (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(0)}K` : `$${v}`)
      : `${v}${suffix}`;
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
          <XAxis dataKey="period" tick={{ fontSize: 11, fill: "#64748B" }} />
          <YAxis tick={{ fontSize: 11, fill: "#64748B" }} tickFormatter={fmt} />
          <Tooltip formatter={(v: number) => fmt(Number(v))}
                   contentStyle={{ borderRadius: 8, border: "1px solid #E2E8F0", fontSize: 12 }} />
          {area ? (
            <Area dataKey={dataKey} stroke={color} strokeWidth={2}
                  fill={color} fillOpacity={0.12} />
          ) : (
            <Line dataKey={dataKey} stroke={color} strokeWidth={2} dot={false} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
