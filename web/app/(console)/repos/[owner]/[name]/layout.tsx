"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { RepoContext } from "@/components/RepoContext";
import { api } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { Repo } from "@/lib/types";

const SUBTABS = [
  { segment: "metadata", label: "Metadata" },
  { segment: "wiki", label: "Wiki" },
];

/** Looks the repo up by its name, then shares it with the subtab pages. */
export default function RepoLayout({ children }: { children: ReactNode }) {
  const { owner, name } = useParams<{ owner: string; name: string }>();
  const pathname = usePathname();
  const fullName = `${owner}/${name}`;
  const [repo, setRepo] = useState<Repo | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .listRepos()
      .then((list) => {
        if (!alive) return;
        const found = list.find((r) => r.full_name === fullName) ?? null;
        setRepo(found);
        setMissing(found === null);
      })
      .catch(() => {
        // API unreachable: stay on the loading state.
      });
    return () => {
      alive = false;
    };
  }, [fullName]);

  if (missing) {
    return (
      <div className="empty-state">
        {fullName} isn&apos;t registered. <Link href="/repos">Back to repos</Link>
      </div>
    );
  }

  return (
    <RepoContext.Provider value={repo}>
      <div id="detail-header">
        <div>
          <span className="pill pill-type-repo">repo</span>
          <h2>{fullName}</h2>
        </div>
        {repo && <div id="detail-meta">registered {fmtTime(repo.created_at)}</div>}
      </div>

      <nav className="subtabs" aria-label="Repo sections">
        {SUBTABS.map((t) => {
          const href = `/repos/${fullName}/${t.segment}`;
          const active = pathname.startsWith(href);
          return (
            <Link
              key={t.segment}
              href={href}
              className={"subtab" + (active ? " active" : "")}
              aria-current={active ? "page" : undefined}
            >
              {t.label}
            </Link>
          );
        })}
      </nav>

      {children}
    </RepoContext.Provider>
  );
}
