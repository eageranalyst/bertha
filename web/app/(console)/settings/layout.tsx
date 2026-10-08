"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Each settings area is its own page. Model providers (local and hosted
// vendors) are meant to join Models here.
const SUBTABS = [
  { href: "/settings", label: "General" },
  { href: "/settings/models", label: "Models" },
];

/** The same main and detail-pane frame as the other sections, with the settings areas as subtabs. */
export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <main>
      <section id="detail-pane">
        <nav className="subtabs" aria-label="Settings sections">
          {SUBTABS.map((t) => {
            const active = t.href === "/settings" ? pathname === "/settings" : pathname.startsWith(t.href);
            return (
              <Link
                key={t.href}
                href={t.href}
                className={"subtab" + (active ? " active" : "")}
                aria-current={active ? "page" : undefined}
              >
                {t.label}
              </Link>
            );
          })}
        </nav>
        {children}
      </section>
    </main>
  );
}
