"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { Repo } from "@/lib/types";

const SUBTABS = [
  { segment: "metadata", label: "Metadata" },
  { segment: "wiki", label: "Wiki" },
];

/** Header and subtabs for one repo. The subtab follows the URL. */
export default function RepoLayout({ children }: { children: ReactNode }) {
  const { id } = useParams<{ id: string }>();
  const pathname = usePathname();
  const repoId = Number(id);
  const [repo, setRepo] = useState<Repo | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .listRepos()
      .then((list) => {
        if (!alive) return;
        const found = list.find((r) => r.id === repoId) ?? null;
        setRepo(found);
        setMissing(found === null);
      })
      .catch(() => {
        // API unreachable: stay on the loading state.
      });
    return () => {
      alive = false;
    };
  }, [repoId]);

  if (missing) {
    return (
      <div className="empty-state">
        That repo isn&apos;t registered. <Link href="/repos">Back to repos</Link>
      </div>
    );
  }

  return (
    <>
      <div id="detail-header">
        <div>
          <span className="pill pill-type-repo">repo</span> <span className="ref">#{id}</span>
          <h2>{repo?.full_name ?? "…"}</h2>
        </div>
        {repo && <div id="detail-meta">registered {fmtTime(repo.created_at)}</div>}
      </div>

      <nav className="subtabs" aria-label="Repo sections">
        {SUBTABS.map((t) => {
          const href = `/repos/${id}/${t.segment}`;
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
    </>
  );
}
