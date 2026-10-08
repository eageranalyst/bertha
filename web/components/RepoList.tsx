"use client";

import { useRouter } from "next/navigation";
import type { Repo } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { WikiStatusPill } from "./WikiStatusPill";

export function RepoList({ repos, selectedName }: { repos: Repo[]; selectedName: string | null }) {
  const router = useRouter();

  return (
    <section id="session-list-pane">
      <div className="list-pane-header">
        <span className="list-pane-title">Registered repos</span>
      </div>
      <div id="session-rows">
        {repos.map((r) => (
          <div
            key={r.id}
            className={"session-row" + (r.full_name === selectedName ? " selected" : "")}
            onClick={() => router.push(`/repos/${r.full_name}/metadata`)}
          >
            <div className="session-row-top">
              <span className="session-row-title">{r.full_name}</span>
              <WikiStatusPill status={r.wiki_status} />
            </div>
            <div className="session-row-meta">
              <span>
                {r.wiki_last_indexed_at
                  ? `last indexed ${fmtTime(r.wiki_last_indexed_at)}`
                  : "never indexed"}
              </span>
            </div>
          </div>
        ))}
      </div>
      {repos.length === 0 && (
        <div className="empty-state">
          No repos registered yet. Add them with FACTORY_REPOS or POST /api/repos.
        </div>
      )}
    </section>
  );
}
