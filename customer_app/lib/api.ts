// Typed client for the InsightHub FastAPI backend.
// NOTE (security): this scaffold stores the JWT in localStorage for simplicity.
// For production, move to httpOnly, SameSite cookies to mitigate XSS token theft.

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ---- token storage ----
const TOKEN_KEY = "ih_token";
const TENANT_KEY = "ih_tenant";

export const auth = {
  get token(): string | null {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(TOKEN_KEY);
  },
  set token(v: string | null) {
    if (typeof window === "undefined") return;
    if (v) window.localStorage.setItem(TOKEN_KEY, v);
    else window.localStorage.removeItem(TOKEN_KEY);
  },
  get tenantId(): number | null {
    if (typeof window === "undefined") return null;
    const v = window.localStorage.getItem(TENANT_KEY);
    if (v) return Number(v);
    // Fall back to the configured default tenant (the reference data is tenant 1).
    const d = process.env.NEXT_PUBLIC_DEFAULT_TENANT;
    return d ? Number(d) : 1;
  },
  set tenantId(v: number | null) {
    if (typeof window === "undefined") return;
    if (v === null) window.localStorage.removeItem(TENANT_KEY);
    else window.localStorage.setItem(TENANT_KEY, String(v));
  },
  logout() {
    this.token = null;
    this.tenantId = null;
  },
};

// ---- core fetch ----
async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string>),
  };
  if (auth.token) headers["Authorization"] = `Bearer ${auth.token}`;

  const res = await fetch(`${API_URL}${path}`, { ...opts, headers });
  if (res.status === 401) {
    auth.logout();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    let detail = body?.detail;
    // FastAPI 422 returns `detail` as an array of {loc, msg, ...}; make it readable.
    if (Array.isArray(detail)) {
      detail = detail.map((d: any) => d?.msg || JSON.stringify(d)).join("; ");
    } else if (detail && typeof detail === "object") {
      detail = JSON.stringify(detail);
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

// ---- types ----
export interface Overview {
  tenant_id: number | null;
  revenue: number;
  costs: number;
  gross_margin_pct: number;
  avg_margin_pct: number;
  transactions: number;
  customers: number;
  net_cash_flow: number;
}
export interface SeriesPoint {
  period: string; year: number; month: number;
  revenue: number; cost: number; net: number;
  cumulative?: number; margin?: number;
}
export interface Entity { name: string; total: number; transactions: number; }
export interface ChatReply { answer: string; grounded: boolean; }
export interface Me { id: number; username: string; role: string; display_name?: string; tenant_id?: number | null; }
export interface Tenant {
  id: number; name: string; slug: string;
  domain_type: string; plan: string; contact_email: string;
}

// ---- endpoints ----
export const api = {
  async login(username: string, password: string) {
    const data = await request<{ access_token: string; token_type: string }>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) }
    );
    auth.token = data.access_token;
    // Resolve which customer (tenant) this login belongs to and pin it,
    // so every page shows only that customer's data.
    try {
      const who = await request<Me>("/auth/me");
      auth.tenantId = who.tenant_id ?? null;
    } catch {
      /* non-fatal — falls back to default tenant */
    }
    return data;
  },
  me: () => request<Me>("/auth/me"),
  tenant: (id: number) => request<Tenant>(`/tenants/${id}`),
  myTenant: () => request<Tenant | null>("/tenants/mine"),
  tenants: () => request<Tenant[]>("/tenants"),
  overview: (t?: number | null) =>
    request<Overview>(`/analytics/overview${t != null ? `?tenant_id=${t}` : ""}`),
  timeseries: (t?: number | null) =>
    request<{ series: SeriesPoint[] }>(`/analytics/timeseries${t != null ? `?tenant_id=${t}` : ""}`),
  topEntities: (t?: number | null, limit = 10) =>
    request<{ entities: Entity[] }>(
      `/analytics/top-entities?limit=${limit}${t != null ? `&tenant_id=${t}` : ""}`),
  suppliers: (t?: number | null, limit = 10) =>
    request<{ suppliers: Entity[] }>(
      `/analytics/suppliers?limit=${limit}${t != null ? `&tenant_id=${t}` : ""}`),
  chat: (message: string, tenantId?: number | null, history: {role: string; content: string}[] = []) =>
    request<ChatReply>("/ai/chat", {
      method: "POST",
      body: JSON.stringify({ message, tenant_id: tenantId ?? null, history }),
    }),

  // ── Ad-hoc datasets (persisted playground) ──
  adhocList: (t?: number | null) =>
    request<AdhocMeta[]>(`/adhoc/datasets${t != null ? `?tenant_id=${t}` : ""}`),
  adhocCreate: (name: string, t: number | null, rows: any[]) =>
    request<{ id: number; name: string; row_count: number; columns: string[] }>(
      "/adhoc/datasets",
      { method: "POST", body: JSON.stringify({ name, tenant_id: t, rows }) }),
  adhocGet: (id: number) =>
    request<AdhocDataset>(`/adhoc/datasets/${id}`),
  adhocDelete: (id: number) =>
    request<{ ok: boolean }>(`/adhoc/datasets/${id}`, { method: "DELETE" }),
  adhocInsights: (id: number) =>
    request<{ insight: string }>(`/adhoc/datasets/${id}/insights`, { method: "POST" }),

  // ── Payment Integrity ──
  piAnalyze: (rows: any[]) =>
    request<PIResult>("/pi/analyze", { method: "POST", body: JSON.stringify({ rows }) }),
  piCreateRun: (name: string, t: number | null, rows: any[]) =>
    request<{ id: number; name: string; result: PIResult }>("/pi/runs",
      { method: "POST", body: JSON.stringify({ name, tenant_id: t, rows }) }),
  piListRuns: (t?: number | null) =>
    request<PIRunMeta[]>(`/pi/runs${t != null ? `?tenant_id=${t}` : ""}`),
  piGetRun: (id: number) =>
    request<{ id: number; name: string; result: PIResult }>(`/pi/runs/${id}`),
  piDeleteRun: (id: number) =>
    request<{ ok: boolean }>(`/pi/runs/${id}`, { method: "DELETE" }),
  piNarrative: (summary: any, top_findings: any[]) =>
    request<{ narrative: string }>("/pi/narrative",
      { method: "POST", body: JSON.stringify({ summary, top_findings }) }),
};

export interface PIRunMeta {
  id: number; name: string; row_count: number; total_paid: number;
  amount_at_risk: number; pct_at_risk: number; flagged_claims: number; created_at: string;
}

export interface PIFinding {
  claim_id: string; provider_npi: string; provider_name: string; cpt: string;
  category: string; severity: string; reason: string;
  amount_at_risk: number; confidence: number; recommendation: string;
}
export interface PIProvider {
  provider_npi: string; provider_name: string; total_paid: number; claims: number;
  members: number; avg_paid: number; flagged_amount: number; risk_score: number; reasons: string[];
}
export interface PIResult {
  summary: {
    total_claims: number; total_paid: number; flagged_claims: number;
    amount_at_risk: number; pct_at_risk: number;
    by_category: { category: string; count: number; amount: number }[];
    providers_flagged: number;
  };
  findings: PIFinding[];
  providers: PIProvider[];
}

export interface AdhocMeta { id: number; name: string; row_count: number; created_at: string; }
export interface AdhocDataset { id: number; name: string; columns: string[]; row_count: number; rows: any[]; }

export function fmtCurrency(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}
