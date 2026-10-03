"use client";

import { useState } from "react";
import type { Repo } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { RepoDetail } from "./RepoDetail";
import { WikiStatusPill } from "./WikiStatusPill";

export function ReposView({ repos }: { repos: Repo[] }) {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = repos.find((r) => r.id === selectedId) ?? repos[0] ?? null;

  return (
    <main>
      <section id="session-list-pane">
        <div className="list-pane-header">
          <span className="list-pane-title">Registered repos</span>
        </div>
        <div id="session-rows">
          {repos.map((r) => (
            <div
              key={r.id}
              className={"session-row" + (selected?.id === r.id ? " selected" : "")}
              onClick={() => setSelectedId(r.id)}
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

      <section id="detail-pane">
        {selected ? (
          // Keyed so switching repos resets the subtab and wiki position.
          <RepoDetail key={selected.id} repo={selected} />
        ) : (
          <div className="empty-state">Select a repo.</div>
        )}
      </section>
    </main>
  );
}
