"use client";

// Domain-adaptive vocabulary for the customer app. The tenant's `domain_type`
// (from the API) selects a config so a SaaS company sees SaaS language, a
// pharmacy sees pharmacy language, etc. — over the same underlying data.
import { useEffect, useState } from "react";
import { api, auth } from "./api";

export interface DomainConfig {
  key: string;
  label: string;                       // "SaaS / Software"
  nav: { href: string; label: string }[];
  revenueWord: string;                 // Sales | Revenue | Income
  costWord: string;                    // Purchases | Costs | Expenses
  customersWord: string;               // Clients | Customers
  suppliersWord: string;               // Suppliers | Vendors
  txnWord: string;                     // Transactions | Invoices | Orders
  // overview KPI card labels
  kpiRevenue: string;
  kpiCost: string;
  kpiCustomers: string;
  kpiTxns: string;
}

const NAV = (revenue: string, cost: string) => [
  { href: "/overview", label: "Overview" },
  { href: "/sales", label: revenue },
  { href: "/purchases", label: cost },
  { href: "/adhoc", label: "Explore" },
  { href: "/ai", label: "AI Insights" },
];

export const DOMAINS: Record<string, DomainConfig> = {
  saas: {
    key: "saas", label: "SaaS / Software",
    nav: NAV("Revenue", "Costs"),
    revenueWord: "Revenue", costWord: "Costs",
    customersWord: "Customers", suppliersWord: "Vendors", txnWord: "Invoices",
    kpiRevenue: "Revenue", kpiCost: "Costs", kpiCustomers: "Customers", kpiTxns: "Invoices",
  },
  retail: {
    key: "retail", label: "Retail / E-commerce",
    nav: NAV("Sales", "Inventory"),
    revenueWord: "Sales", costWord: "Inventory",
    customersWord: "Customers", suppliersWord: "Suppliers", txnWord: "Orders",
    kpiRevenue: "Sales", kpiCost: "Cost of Goods", kpiCustomers: "Customers", kpiTxns: "Orders",
  },
  pharmacy: {
    key: "pharmacy", label: "Pharmacy / Medical",
    nav: NAV("Sales", "Purchases"),
    revenueWord: "Sales", costWord: "Purchases",
    customersWord: "Customers", suppliersWord: "Suppliers", txnWord: "Bills",
    kpiRevenue: "Sales", kpiCost: "Purchases", kpiCustomers: "Customers", kpiTxns: "Bills",
  },
  accounting: {
    key: "accounting", label: "Accounting / Finance",
    nav: NAV("Income", "Expenses"),
    revenueWord: "Income", costWord: "Expenses",
    customersWord: "Clients", suppliersWord: "Vendors", txnWord: "Entries",
    kpiRevenue: "Income", kpiCost: "Expenses", kpiCustomers: "Clients", kpiTxns: "Entries",
  },
  healthcare: {
    key: "healthcare", label: "Healthcare / Insurance",
    nav: [
      { href: "/overview", label: "Overview" },
      { href: "/pi", label: "Payment Integrity" },
      { href: "/adhoc", label: "Explore" },
      { href: "/ai", label: "AI Insights" },
    ],
    revenueWord: "Paid", costWord: "Recoveries",
    customersWord: "Providers", suppliersWord: "Providers", txnWord: "Claims",
    kpiRevenue: "Paid", kpiCost: "Recoveries", kpiCustomers: "Providers", kpiTxns: "Claims",
  },
  generic: {
    key: "generic", label: "General Business",
    nav: NAV("Sales", "Purchases"),
    revenueWord: "Sales", costWord: "Purchases",
    customersWord: "Clients", suppliersWord: "Suppliers", txnWord: "Transactions",
    kpiRevenue: "Revenue", kpiCost: "Costs", kpiCustomers: "Customers", kpiTxns: "Transactions",
  },
};

export function domainFor(domainType?: string | null): DomainConfig {
  return DOMAINS[(domainType || "generic").toLowerCase()] ?? DOMAINS.generic;
}

/** Client hook: resolves the current (viewed) tenant's domain config.
 *  Uses /tenants/mine (works for customer logins) and falls back to the
 *  admin-only /tenants/{id} for the operator preview. */
export function useDomain(): DomainConfig {
  const [cfg, setCfg] = useState<DomainConfig>(DOMAINS.generic);
  useEffect(() => {
    api.myTenant()
      .then((tn) => {
        if (tn && tn.domain_type) { setCfg(domainFor(tn.domain_type)); return; }
        const t = auth.tenantId;
        if (t != null) api.tenant(t).then((x) => setCfg(domainFor(x.domain_type))).catch(() => {});
      })
      .catch(() => {
        const t = auth.tenantId;
        if (t != null) api.tenant(t).then((x) => setCfg(domainFor(x.domain_type))).catch(() => {});
      });
  }, []);
  return cfg;
}
