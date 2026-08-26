"use client";

import { useEffect, useState } from "react";
import { api, auth, type Me, type Tenant } from "@/lib/api";

const DOMAIN_LABEL: Record<string, string> = {
  pharmacy: "Pharmacy / Medical",
  retail: "Retail / E-commerce",
  saas: "SaaS / Software",
  accounting: "Accounting / Finance",
  generic: "General Business",
};

export default function TopBar() {
  const [me, setMe] = useState<Me | null>(null);
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [allTenants, setAllTenants] = useState<Tenant[]>([]);

  // A login with no tenant_id is an operator/admin → can preview any customer.
  const isOperator = me != null && (me.tenant_id === null || me.tenant_id === undefined);
  const viewedId = auth.tenantId;

  useEffect(() => {
    api.me()
      .then((m) => {
        setMe(m);
        if (m.tenant_id === null || m.tenant_id === undefined) {
          // operator/admin — can list all customers + read any tenant
          api.tenants().then(setAllTenants).catch(() => {});
          const v = auth.tenantId;
          if (v != null) api.tenant(v).then(setTenant).catch(() => {});
        } else {
          // customer login — read only its own tenant (no admin rights)
          api.myTenant().then((t) => t && setTenant(t)).catch(() => {});
        }
      })
      .catch(() => {});
  }, []);

  function switchTenant(id: number) {
    auth.tenantId = id;
    window.location.reload(); // reloads every page's data for the chosen customer
  }

  const initials = (tenant?.name || "IH")
    .split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  return (
    <header className="flex items-center justify-between border-b border-slate-200 bg-white px-8 py-3">
      {/* Company + domain (+ operator switcher) */}
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand text-sm font-bold text-white">
          {initials}
        </div>
        {isOperator ? (
          <div>
            <div className="mb-0.5 flex items-center gap-2">
              <span className="text-[0.7rem] font-semibold uppercase tracking-wide text-amber">
                Admin preview
              </span>
            </div>
            <select
              value={viewedId ?? ""}
              onChange={(e) => switchTenant(Number(e.target.value))}
              className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm font-semibold text-navy focus:border-brand focus:outline-none"
            >
              {allTenants.length === 0 && <option value="">Loading customers…</option>}
              {allTenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} · {DOMAIN_LABEL[t.domain_type] ?? t.domain_type}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div>
            <div className="text-sm font-semibold text-navy">
              {tenant?.name ?? "Your Company"}
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span>{tenant ? DOMAIN_LABEL[tenant.domain_type] ?? tenant.domain_type : "—"}</span>
              {tenant?.plan && (
                <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium capitalize text-slate-600">
                  {tenant.plan}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* User */}
      <div className="flex items-center gap-3">
        <div className="text-right">
          <div className="text-sm font-medium text-navy">
            {me?.display_name || me?.username || "—"}
          </div>
          <div className="text-xs capitalize text-slate-500">
            {isOperator ? "Operator · all customers" : (me?.role ?? "")}
          </div>
        </div>
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-navy text-sm font-bold text-white">
          {(me?.username || "U")[0].toUpperCase()}
        </div>
        <button
          onClick={() => { auth.logout(); window.location.href = "/login"; }}
          className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:border-danger hover:text-danger"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
