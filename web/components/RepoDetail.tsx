"use client";

import { useState } from "react";
import type { Repo } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { RepoMetadataPanel } from "./RepoMetadataPanel";
import { RepoWiki } from "./RepoWiki";

type Subtab = "metadata" | "wiki";

const SUBTABS: { id: Subtab; label: string }[] = [
  { id: "metadata", label: "Metadata" },
  { id: "wiki", label: "Wiki" },
];

export function RepoDetail({ repo }: { repo: Repo }) {
  const [subtab, setSubtab] = useState<Subtab>("metadata");

  return (
    <>
      <div id="detail-header">
        <div>
          <span className="pill pill-type-repo">repo</span>{" "}
          <span className="ref">#{repo.id}</span>
          <h2>{repo.full_name}</h2>
        </div>
        <div id="detail-meta">registered {fmtTime(repo.created_at)}</div>
      </div>

      <nav className="subtabs" role="tablist">
        {SUBTABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={subtab === t.id}
            className={"subtab" + (subtab === t.id ? " active" : "")}
            onClick={() => setSubtab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {subtab === "metadata" ? <RepoMetadataPanel repoId={repo.id} /> : <RepoWiki repoId={repo.id} />}
    </>
  );
}
