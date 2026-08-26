// Ad-hoc analysis engine — profiles ANY tabular data and computes aggregates,
// entirely in the browser. No schema, no domain assumptions.

export type ColType = "number" | "date" | "category" | "text";

export interface ColProfile {
  name: string;
  type: ColType;
  count: number;      // non-null values
  nulls: number;
  unique: number;
  // numeric
  min?: number;
  max?: number;
  mean?: number;
  sum?: number;
  // category
  top?: { value: string; count: number }[];
  // date
  minDate?: string;
  maxDate?: string;
}

export type Row = Record<string, any>;

const isBlank = (v: any) => v === null || v === undefined || v === "";

function looksNumeric(v: any): boolean {
  if (typeof v === "number") return Number.isFinite(v);
  if (typeof v !== "string") return false;
  const s = v.trim().replace(/[$,%]/g, "");
  return s !== "" && !isNaN(Number(s));
}
function toNum(v: any): number {
  if (typeof v === "number") return v;
  return Number(String(v).trim().replace(/[$,%]/g, ""));
}
function looksDate(v: any): boolean {
  if (typeof v !== "string") return false;
  const s = v.trim();
  // YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY, ISO timestamps
  if (/^\d{4}-\d{1,2}-\d{1,2}/.test(s)) return true;
  if (/^\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}/.test(s)) return true;
  return false;
}

export function inferType(values: any[]): ColType {
  const nonNull = values.filter((v) => !isBlank(v));
  if (nonNull.length === 0) return "text";
  const sample = nonNull.slice(0, 200);
  const numFrac = sample.filter(looksNumeric).length / sample.length;
  const dateFrac = sample.filter(looksDate).length / sample.length;
  if (dateFrac >= 0.8) return "date";
  if (numFrac >= 0.8) return "number";
  const uniq = new Set(nonNull.map(String)).size;
  // low-cardinality strings → category, else free text
  if (uniq <= Math.max(20, nonNull.length * 0.5)) return "category";
  return "text";
}

export function profileColumns(rows: Row[]): ColProfile[] {
  if (!rows.length) return [];
  const cols = Object.keys(rows[0]);
  return cols.map((name) => {
    const values = rows.map((r) => r[name]);
    const nonNull = values.filter((v) => !isBlank(v));
    const type = inferType(values);
    const base: ColProfile = {
      name, type,
      count: nonNull.length,
      nulls: values.length - nonNull.length,
      unique: new Set(nonNull.map(String)).size,
    };
    if (type === "number") {
      const nums = nonNull.map(toNum).filter(Number.isFinite);
      if (nums.length) {
        base.min = Math.min(...nums);
        base.max = Math.max(...nums);
        base.sum = nums.reduce((a, b) => a + b, 0);
        base.mean = base.sum / nums.length;
      }
    } else if (type === "date") {
      const ds = nonNull.map((v) => new Date(v)).filter((d) => !isNaN(+d)).sort((a, b) => +a - +b);
      if (ds.length) {
        base.minDate = ds[0].toISOString().slice(0, 10);
        base.maxDate = ds[ds.length - 1].toISOString().slice(0, 10);
      }
    } else {
      const counts: Record<string, number> = {};
      for (const v of nonNull) counts[String(v)] = (counts[String(v)] || 0) + 1;
      base.top = Object.entries(counts)
        .map(([value, count]) => ({ value, count }))
        .sort((a, b) => b.count - a.count)
        .slice(0, 8);
    }
    return base;
  });
}

export type Agg = "sum" | "avg" | "count" | "min" | "max";

/** Group `rows` by `dim`, aggregating `measure` (ignored for count). */
export function aggregate(rows: Row[], dim: string, measure: string, agg: Agg, limit = 20)
  : { name: string; value: number }[] {
  const groups: Record<string, number[]> = {};
  for (const r of rows) {
    const key = isBlank(r[dim]) ? "(blank)" : String(r[dim]);
    (groups[key] ||= []).push(looksNumeric(r[measure]) ? toNum(r[measure]) : 0);
  }
  const reduce = (arr: number[]): number => {
    if (agg === "count") return arr.length;
    if (!arr.length) return 0;
    if (agg === "sum") return arr.reduce((a, b) => a + b, 0);
    if (agg === "avg") return arr.reduce((a, b) => a + b, 0) / arr.length;
    if (agg === "min") return Math.min(...arr);
    return Math.max(...arr);
  };
  return Object.entries(groups)
    .map(([name, arr]) => ({ name, value: Math.round(reduce(arr) * 100) / 100 }))
    .sort((a, b) => b.value - a.value)
    .slice(0, limit);
}

/** Simple equal-width histogram for a numeric column. */
export function histogram(rows: Row[], col: string, bins = 10): { name: string; value: number }[] {
  const nums = rows.map((r) => r[col]).filter(looksNumeric).map(toNum);
  if (!nums.length) return [];
  const min = Math.min(...nums), max = Math.max(...nums);
  if (min === max) return [{ name: String(min), value: nums.length }];
  const w = (max - min) / bins;
  const out = Array.from({ length: bins }, (_, i) => ({
    name: `${(min + i * w).toFixed(1)}`, value: 0,
  }));
  for (const n of nums) {
    let idx = Math.floor((n - min) / w);
    if (idx >= bins) idx = bins - 1;
    out[idx].value++;
  }
  return out;
}

/** Monthly time series: date column + numeric measure → sum per month. */
export function timeSeries(rows: Row[], dateCol: string, measure: string)
  : { name: string; value: number }[] {
  const groups: Record<string, number> = {};
  for (const r of rows) {
    if (isBlank(r[dateCol])) continue;
    const d = new Date(r[dateCol]);
    if (isNaN(+d)) continue;
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    groups[key] = (groups[key] || 0) + (looksNumeric(r[measure]) ? toNum(r[measure]) : 0);
  }
  return Object.entries(groups)
    .map(([name, value]) => ({ name, value: Math.round(value * 100) / 100 }))
    .sort((a, b) => a.name.localeCompare(b.name));
}
