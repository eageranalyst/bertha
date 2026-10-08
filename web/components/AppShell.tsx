"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { FactoryLogo } from "@/components/images/FactoryMascot";
import { ThemeSwitch } from "@/components/ThemeSwitch";

const TABS = [
  { href: "/sessions", label: "Sessions" },
  { href: "/repos", label: "Repos" },
  { href: "/schedules", label: "Schedules" },
  { href: "/skills", label: "Skills" },
  { href: "/models", label: "Models" },
  { href: "/settings", label: "Settings" },
];

/** The header with the section tabs. The active tab follows the URL. */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div id="app">
      <header>
        <div className="brand">
          <span className="brand-mark">
            <FactoryLogo />
          </span>
          <div>
            <h1>Bertha</h1>
            <p className="subtitle">She builds...</p>
          </div>
        </div>
        <div className="header-end">
          <nav className="top-tabs" aria-label="Sections">
            {TABS.map((t) => {
              const active = pathname === t.href || pathname.startsWith(`${t.href}/`);
              return (
                <Link
                  key={t.href}
                  href={t.href}
                  className={"top-tab" + (active ? " active" : "")}
                  aria-current={active ? "page" : undefined}
                >
                  {t.label}
                </Link>
              );
            })}
          </nav>
          <ThemeSwitch />
        </div>
      </header>
      {children}
    </div>
  );
}
