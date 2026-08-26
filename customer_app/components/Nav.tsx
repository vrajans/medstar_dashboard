"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { auth } from "@/lib/api";
import { useDomain } from "@/lib/domains";

export default function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const domain = useDomain();
  const LINKS = domain.nav;

  function logout() {
    auth.logout();
    router.push("/login");
  }

  return (
    <aside className="flex w-56 shrink-0 flex-col justify-between bg-navy p-4 text-white">
      <div>
        <div className="mb-8 px-2 text-xl font-bold">InsightHub</div>
        <nav className="space-y-1">
          {LINKS.map((l) => {
            const active = pathname === l.href;
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`block rounded-lg px-3 py-2 text-sm font-medium transition ${
                  active ? "bg-brand text-white" : "text-slate-300 hover:bg-white/10"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <button
        onClick={logout}
        className="rounded-lg px-3 py-2 text-left text-sm text-slate-400 hover:bg-white/10"
      >
        Sign out
      </button>
    </aside>
  );
}
