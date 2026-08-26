"use client";

import { useEffect, useMemo, useState } from "react";
import Papa from "papaparse";
import { api, auth, type AdhocMeta } from "@/lib/api";
import Section from "@/components/Section";
import SimpleChart from "@/components/SimpleChart";
import {
  profileColumns, aggregate, histogram, timeSeries,
  type Row, type ColProfile, type Agg,
} from "@/lib/adhoc";

const SHEETJS_CDN = "https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js";
function loadSheetJS(): Promise<any> {
  return new Promise((resolve, reject) => {
    if (typeof window === "undefined") return reject(new Error("no window"));
    if ((window as any).XLSX) return resolve((window as any).XLSX);
    const s = document.createElement("script");
    s.src = SHEETJS_CDN; s.async = true;
    s.onload = () => resolve((window as any).XLSX);
    s.onerror = () => reject(new Error("Failed to load Excel parser"));
    document.head.appendChild(s);
  });
}

export default function AdhocPage() {
  const [saved, setSaved] = useState<AdhocMeta[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [openName, setOpenName] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [pending, setPending] = useState<{ name: string; rows: Row[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [insight, setInsight] = useState<string | null>(null);

  const profiles = useMemo(() => profileColumns(rows), [rows]);
  const numberCols = profiles.filter((p) => p.type === "number").map((p) => p.name);
  const catCols = profiles.filter((p) => p.type === "category").map((p) => p.name);
  const dateCols = profiles.filter((p) => p.type === "date").map((p) => p.name);

  const [dim, setDim] = useState("");
  const [measure, setMeasure] = useState("");
  const [agg, setAgg] = useState<Agg>("sum");

  async function refreshList() {
    try { setSaved(await api.adhocList(auth.tenantId)); } catch { /* ignore */ }
  }
  useEffect(() => { refreshList(); }, []);

  function afterParse(name: string, data: Row[]) {
    setPending({ name, rows: data });
    setRows(data); setOpenId(null); setOpenName(name); setInsight(null);
    const p = profileColumns(data);
    setDim(p.find((c) => c.type === "category" || c.type === "date")?.name || p[0]?.name || "");
    setMeasure(p.find((c) => c.type === "number")?.name || "");
  }

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    const name = file.name.replace(/\.[^.]+$/, "");
    if (file.name.toLowerCase().endsWith(".csv")) {
      Papa.parse<Row>(file, {
        header: true, dynamicTyping: true, skipEmptyLines: true,
        complete: (res) => afterParse(name, res.data as Row[]),
        error: (err) => setError(err.message),
      });
    } else {
      loadSheetJS().then((XLSX: any) => {
        const reader = new FileReader();
        reader.onload = (ev) => {
          try {
            const wb = XLSX.read(ev.target?.result, { type: "array" });
            afterParse(name, XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]]) as Row[]);
          } catch (er: any) { setError("Could not read this file: " + er.message); }
        };
        reader.readAsArrayBuffer(file);
      }).catch(() => setError("Excel support unavailable — save as .csv and re-upload."));
    }
  }

  async function saveDataset() {
    if (!pending) return;
    setBusy(true); setError(null);
    try {
      const res = await api.adhocCreate(pending.name, auth.tenantId, pending.rows);
      setPending(null); setOpenId(res.id);
      await refreshList();
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function openSaved(id: number, name: string) {
    setBusy(true); setError(null); setInsight(null); setPending(null);
    try {
      const ds = await api.adhocGet(id);
      setRows(ds.rows); setOpenId(id); setOpenName(name);
      const p = profileColumns(ds.rows);
      setDim(p.find((c) => c.type === "category" || c.type === "date")?.name || p[0]?.name || "");
      setMeasure(p.find((c) => c.type === "number")?.name || "");
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function removeSaved(id: number) {
    try { await api.adhocDelete(id); if (openId === id) { setRows([]); setOpenId(null); } await refreshList(); }
    catch (e: any) { setError(e.message); }
  }

  async function getInsight() {
    if (openId == null) return;
    setBusy(true); setInsight(null);
    try { setInsight((await api.adhocInsights(openId)).insight); }
    catch (e: any) { setInsight("⚠️ " + e.message); }
    finally { setBusy(false); }
  }

  const explorer = useMemo(
    () => (dim ? aggregate(rows, dim, measure, agg) : []),
    [rows, dim, measure, agg]
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-navy">Explore</h1>
        <p className="text-sm text-slate-500">
          Upload any dataset that fits no template — save it to your warehouse and it stays,
          governed and re-openable, with AI insights.
        </p>
      </div>

      {/* Saved datasets */}
      <Section title="Your saved datasets">
        {saved.length === 0 ? (
          <div className="py-4 text-sm text-slate-400">Nothing saved yet — upload a file below and Save it.</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {saved.map((s) => (
              <div key={s.id} className="flex items-center justify-between py-2">
                <button onClick={() => openSaved(s.id, s.name)}
                        className={`text-left text-sm font-medium ${openId === s.id ? "text-brand" : "text-navy hover:text-brand"}`}>
                  {s.name}
                  <span className="ml-2 text-xs font-normal text-slate-400">
                    {s.row_count.toLocaleString()} rows · {s.created_at}
                  </span>
                </button>
                <button onClick={() => removeSaved(s.id)}
                        className="text-xs text-slate-400 hover:text-danger">Delete</button>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Upload */}
      <div className="rounded-xl border-2 border-dashed border-slate-300 bg-white p-6 text-center">
        <input id="adhoc-file" type="file" accept=".csv,.xlsx,.xls" onChange={handleFile} className="hidden" />
        <label htmlFor="adhoc-file"
               className="cursor-pointer rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700">
          Choose a CSV or Excel file
        </label>
        {pending && (
          <div className="mt-3 flex items-center justify-center gap-3">
            <span className="text-sm text-slate-600">
              {pending.name} · {pending.rows.length.toLocaleString()} rows — preview below
            </span>
            <button onClick={saveDataset} disabled={busy}
                    className="rounded-lg bg-ok px-4 py-1.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60"
                    style={{ background: "#059669" }}>
              {busy ? "Saving…" : "Save to warehouse"}
            </button>
          </div>
        )}
        {error && <div className="mt-3 text-sm text-danger">{error}</div>}
      </div>

      {rows.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-navy">
              {openName} {openId != null && <span className="text-xs font-normal text-ok" style={{ color: "#059669" }}>· saved</span>}
            </h2>
            {openId != null && (
              <button onClick={getInsight} disabled={busy}
                      className="rounded-lg border border-brand px-3 py-1.5 text-sm font-semibold text-brand hover:bg-blue-50 disabled:opacity-60">
                {busy ? "Analyzing…" : "✨ AI insights"}
              </button>
            )}
          </div>

          {insight && (
            <div className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm text-navy whitespace-pre-wrap">
              {insight}
            </div>
          )}

          <Section title={`Data profile · ${profiles.length} columns · ${rows.length.toLocaleString()} rows`}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-slate-400">
                    <th className="pb-2">Column</th><th className="pb-2">Type</th>
                    <th className="pb-2 text-right">Filled</th><th className="pb-2 text-right">Unique</th>
                    <th className="pb-2">Summary</th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.map((p) => (
                    <tr key={p.name} className="border-t border-slate-100">
                      <td className="py-2 font-medium text-navy">{p.name}</td>
                      <td className="py-2"><TypeBadge t={p.type} /></td>
                      <td className="py-2 text-right tabular-nums text-slate-500">{p.count}</td>
                      <td className="py-2 text-right tabular-nums text-slate-500">{p.unique}</td>
                      <td className="py-2 text-slate-600">{summary(p)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Build a chart">
            <div className="mb-4 flex flex-wrap items-end gap-3">
              <Field label="Group by">
                <select value={dim} onChange={(e) => setDim(e.target.value)} className={selCls}>
                  {[...catCols, ...dateCols, ...numberCols].map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </Field>
              <Field label="Measure">
                <select value={measure} onChange={(e) => setMeasure(e.target.value)} className={selCls}>
                  <option value="">— (count rows)</option>
                  {numberCols.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </Field>
              <Field label="Aggregate">
                <select value={agg} onChange={(e) => setAgg(e.target.value as Agg)} className={selCls}>
                  {["sum", "avg", "count", "min", "max"].map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </Field>
            </div>
            {explorer.length
              ? <SimpleChart data={explorer} horizontal={explorer.length > 6} height={Math.max(260, explorer.length * 26)} />
              : <div className="py-10 text-center text-sm text-slate-400">Pick a column to group by.</div>}
          </Section>

          {dateCols.length > 0 && numberCols.length > 0 && (
            <Section title={`Trend · ${numberCols[0]} over ${dateCols[0]}`}>
              <SimpleChart data={timeSeries(rows, dateCols[0], numberCols[0])} kind="line" color="#0D9488" />
            </Section>
          )}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {catCols.slice(0, 2).map((c) => (
              <Section key={c} title={`Top values · ${c}`}>
                <SimpleChart data={aggregate(rows, c, "", "count", 8)} horizontal color="#7C3AED" />
              </Section>
            ))}
            {numberCols.slice(0, 2).map((c) => (
              <Section key={c} title={`Distribution · ${c}`}>
                <SimpleChart data={histogram(rows, c)} color="#2563EB" />
              </Section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

const selCls =
  "rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-navy focus:border-brand focus:outline-none";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      {children}
    </div>
  );
}
function TypeBadge({ t }: { t: string }) {
  const map: Record<string, string> = {
    number: "bg-blue-50 text-blue-700", date: "bg-teal-50 text-teal-700",
    category: "bg-violet-50 text-violet-700", text: "bg-slate-100 text-slate-600",
  };
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${map[t] || map.text}`}>{t}</span>;
}
function summary(p: ColProfile): string {
  if (p.type === "number" && p.mean !== undefined)
    return `min ${round(p.min)} · avg ${round(p.mean)} · max ${round(p.max)}`;
  if (p.type === "date") return `${p.minDate} → ${p.maxDate}`;
  if (p.top?.length) return p.top.slice(0, 3).map((t) => `${t.value} (${t.count})`).join(", ");
  return "—";
}
function round(n?: number) { return n === undefined ? "—" : Math.round(n * 100) / 100; }
