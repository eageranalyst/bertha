"use client";

import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { RepoMetadata } from "@/lib/types";
import { WikiStatusPill } from "./WikiStatusPill";

function Card({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="meta-card">
      <div className="meta-label">{label}</div>
      <div className="meta-value">{children}</div>
    </div>
  );
}

export function RepoMetadataPanel({ repoId }: { repoId: number }) {
  const [data, setData] = useState<RepoMetadata | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getRepoMetadata(repoId)
      .then((d) => alive && setData(d))
      .catch((err: Error) => alive && setError(err.message));
    return () => {
      alive = false;
    };
  }, [repoId]);

  if (error) return <div className="empty-state">Could not load metadata: {error}</div>;
  if (!data) return <div className="empty-state">Loading…</div>;

  const gh = data.github;
  const scan = data.wiki?.scan ?? null;

  return (
    <div className="meta-panel">
      {data.github_error && (
        <div className="wiki-error">GitHub details unavailable: {data.github_error}</div>
      )}

      <h3 className="meta-section">Repository</h3>
      <div className="meta-grid">
        <Card label="GitHub">
          {gh ? (
            <a href={gh.html_url} target="_blank" rel="noreferrer">
              {data.repo.full_name}
            </a>
          ) : (
            data.repo.full_name
          )}
        </Card>
        <Card label="Visibility">
          {gh ? `${gh.private ? "Private" : "Public"}${gh.archived ? " · archived" : ""}` : "—"}
        </Card>
        <Card label="Default branch">{gh?.default_branch ?? "—"}</Card>
        <Card label="Primary language (GitHub)">{gh?.language ?? "—"}</Card>
        <Card label="Last pushed">{gh ? fmtTime(gh.pushed_at) : "—"}</Card>
        <Card label="Open issues & PRs">{gh ? gh.open_issues : "—"}</Card>
        <Card label="Description">{gh?.description || "—"}</Card>
        <Card label="Registered">{fmtTime(data.repo.created_at)}</Card>
      </div>

      <h3 className="meta-section">Sessions</h3>
      <div className="meta-grid">
        <Card label="Total">{data.sessions.total}</Card>
        <Card label="Active">{data.sessions.active}</Card>
        <Card label="With a PR">{data.sessions.with_pr}</Card>
      </div>

      <h3 className="meta-section">Wiki</h3>
      <div className="meta-grid">
        <Card label="Status">
          <WikiStatusPill status={data.wiki?.status ?? null} />
        </Card>
        <Card label="Created">{data.wiki ? fmtTime(data.wiki.created_at) : "—"}</Card>
        <Card label="Last indexed">{data.wiki ? fmtTime(data.wiki.last_indexed_at) : "—"}</Card>
        <Card label="Updated">{data.wiki ? fmtTime(data.wiki.updated_at) : "—"}</Card>
        <Card label="Commit">
          <span className="ref">{data.wiki?.commit_sha?.slice(0, 7) ?? "—"}</span>
        </Card>
      </div>

      <h3 className="meta-section">Codebase scan</h3>
      {scan ? (
        <div className="meta-grid">
          <Card label="Files">{scan.file_count}</Card>
          <Card label="Languages">
            {scan.languages.length
              ? scan.languages.map((l) => `${l.name} ${l.percent}%`).join(", ")
              : "—"}
          </Card>
          <Card label="Frameworks">{scan.frameworks.join(", ") || "—"}</Card>
          <Card label="Infrastructure">{scan.infra.join(", ") || "—"}</Card>
          <Card label="Ecosystems">{scan.ecosystems.join(", ") || "—"}</Card>
          <Card label="Docs">{scan.docs.length}</Card>
          <Card label="Decision records">{scan.decisions.length}</Card>
          <Card label="Data-model files">{scan.data_files.length}</Card>
          <Card label="Test files">{scan.test_files.length}</Card>
        </div>
      ) : (
        <div className="empty-state">Not scanned yet. Index the wiki to scan the codebase.</div>
      )}
    </div>
  );
}
