"use client";

import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { fmtAgo, fmtTime } from "@/lib/format";
import type { RepoMetadata, WikiScan } from "@/lib/types";
import { WikiStatusPill } from "./WikiStatusPill";

// Languages past this many fold into "Other": the palette has five slots.
const LANGUAGE_SLOTS = 5;

/** A repo's languages as one 100% bar, with a legend that always shows the values. */
function LanguageBar({ languages }: { languages: WikiScan["languages"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = languages.slice(0, LANGUAGE_SLOTS);
  const rest = languages.slice(LANGUAGE_SLOTS).reduce((sum, l) => sum + l.percent, 0);
  const parts = [
    ...shown.map((l, i) => ({ name: l.name, percent: l.percent, color: `var(--series-${i + 1})` })),
    ...(rest > 0 ? [{ name: "Other", percent: Math.round(rest * 10) / 10, color: "var(--series-other)" }] : []),
  ];
  return (
    <div className="lang">
      <div className="lang-bar" role="img" aria-label={parts.map((p) => `${p.name} ${p.percent}%`).join(", ")}>
        {parts.map((p, i) => (
          <span
            key={p.name}
            className={"lang-seg" + (hover !== null && hover !== i ? " dim" : "")}
            style={{ flexGrow: p.percent, background: p.color }}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          >
            {hover === i && (
              <span className="lang-tip">
                {p.name} <b>{p.percent}%</b>
              </span>
            )}
          </span>
        ))}
      </div>
      <ul className="lang-legend">
        {parts.map((p, i) => (
          <li key={p.name} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <span className="lang-swatch" style={{ background: p.color }} />
            {p.name}
            <span className="lang-pct">{p.percent}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Stat({ value, label, note }: { value: ReactNode; label: string; note?: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

function Chips({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="chip-group">
      <div className="chip-label">{label}</div>
      <div className="chips">
        {items.map((item) => (
          <span key={item} className="chip">
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Files per top-level folder: one series, so one hue, bars from a shared zero. */
function FolderBars({ dirs }: { dirs: { name: string; files: number }[] }) {
  const top = dirs.slice(0, 6);
  const max = Math.max(...top.map((d) => d.files), 1);
  return (
    <ul className="folders">
      {top.map((d) => (
        <li key={d.name} title={`${d.name}: ${d.files} files`}>
          <span className="folder-name">{d.name}</span>
          <span className="folder-track">
            <span className="folder-bar" style={{ width: `${(d.files / max) * 100}%` }} />
          </span>
          <span className="folder-count">{d.files}</span>
        </li>
      ))}
    </ul>
  );
}

type Readiness = "ok" | "warn" | "unknown";

function Check({ state, title, children }: { state: Readiness; title: string; children: ReactNode }) {
  const mark = { ok: "✓", warn: "!", unknown: "?" }[state];
  const label = { ok: "Good", warn: "Worth a look", unknown: "Unknown" }[state];
  return (
    <li className={`ready ready-${state}`}>
      <span className="ready-mark" aria-label={label}>
        {mark}
      </span>
      <div>
        <div className="ready-title">{title}</div>
        <div className="ready-detail">{children}</div>
      </div>
    </li>
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

  if (error && !data) return <div className="empty-state">Could not load metadata: {error}</div>;
  if (!data) return <div className="empty-state">Loading…</div>;

  const gh = data.github;
  const wiki = data.wiki;
  const scan = wiki?.scan ?? null;
  const protectedBranch = gh?.default_branch_protected;

  return (
    <div className="meta-panel">
      {data.github_error && <div className="wiki-error">GitHub details unavailable: {data.github_error}</div>}
      {error && <div className="wiki-error">{error}</div>}

      <section className="repo-hero">
        <div className="repo-hero-top">
          {gh ? (
            <a className="repo-hero-name" href={gh.html_url} target="_blank" rel="noreferrer">
              {data.repo.full_name} ↗
            </a>
          ) : (
            <span className="repo-hero-name">{data.repo.full_name}</span>
          )}
          {gh && <span className="chip chip-quiet">{gh.private ? "Private" : "Public"}</span>}
          {gh?.archived && <span className="chip chip-warn">Archived</span>}
          {gh && (
            <span className="chip chip-quiet" title="Default branch">
              ⎇ {gh.default_branch}
            </span>
          )}
        </div>
        <p className={"repo-hero-desc" + (gh?.description ? "" : " empty")}>
          {gh?.description || "No description on GitHub."}
        </p>
        <div className="repo-hero-meta">
          {gh?.pushed_at && <span title={fmtTime(gh.pushed_at)}>Pushed {fmtAgo(gh.pushed_at)}</span>}
          <span title={fmtTime(data.repo.created_at)}>Registered {fmtAgo(data.repo.created_at)}</span>
          {scan && <span>Scanned at {wiki?.commit_sha?.slice(0, 7)}</span>}
        </div>
        {scan && scan.languages.length > 0 && <LanguageBar languages={scan.languages} />}
      </section>

      <div className="stat-row">
        <Stat value={scan?.file_count ?? "—"} label="Files" />
        <Stat value={gh?.open_issues ?? "—"} label="Open issues & PRs" />
        <Stat
          value={data.sessions.total}
          label="Sessions"
          note={`${data.sessions.active} active · ${data.sessions.with_pr} with a PR`}
        />
        <Stat value={scan?.test_files.length ?? "—"} label="Test files" />
      </div>

      <section className="meta-card-block">
        <h3 className="meta-section">Code review</h3>
        <label className="form-check auto-review">
          <input
            type="checkbox"
            checked={Boolean(data.repo.auto_review)}
            onChange={async (e) => {
              const on = e.target.checked;
              try {
                const repo = await api.setAutoReview(repoId, on);
                setData((d) => (d ? { ...d, repo } : d));
              } catch (err) {
                setError((err as Error).message);
              }
            }}
          />
          <span>
            Review every session&apos;s pull request automatically. When Bertha first pushes to a PR, QC (the
            factory&apos;s quality control) reviews it in its own session, posts what it finds on the PR, and
            Bertha fixes the findings straight away. QC checks her fixes too, up to three reviews per
            session. Off, each session with a PR has a Run code review button instead.
          </span>
        </label>
      </section>

      {scan ? (
        <div className="meta-columns">
          <section className="meta-card-block">
            <h3 className="meta-section">Stack</h3>
            <Chips label="Frameworks" items={scan.frameworks} />
            <Chips label="Infrastructure" items={scan.infra} />
            <Chips label="Ecosystems" items={scan.ecosystems} />
            <Chips label="Entry points" items={scan.entrypoints} />
          </section>
          {scan.top_dirs && scan.top_dirs.length > 0 && (
            <section className="meta-card-block">
              <h3 className="meta-section">Where the files are</h3>
              <FolderBars dirs={scan.top_dirs} />
            </section>
          )}
        </div>
      ) : (
        <div className="empty-state">Not scanned yet. Index the wiki to scan the codebase.</div>
      )}

      <section className="meta-card-block">
        <h3 className="meta-section">Ready for agents</h3>
        <ul className="ready-list">
          <Check
            state={protectedBranch ? "ok" : protectedBranch === false ? "warn" : "unknown"}
            title="Default branch protected"
          >
            {protectedBranch
              ? "Agent PRs need a review before they merge."
              : protectedBranch === false
                ? "Nothing stops a PR merging unreviewed. Protect the default branch on GitHub."
                : "The token can't see branch protection."}
          </Check>
          <Check state={scan?.workflows.length ? "ok" : scan ? "warn" : "unknown"} title="CI">
            {scan?.workflows.length
              ? `${scan.workflows.length} workflow${scan.workflows.length === 1 ? "" : "s"}, so Bertha can see and fix failing checks.`
              : scan
                ? "No GitHub Actions workflows, so nothing checks Bertha's PRs."
                : "Not scanned yet."}
          </Check>
          <Check state={scan?.test_files.length ? "ok" : scan ? "warn" : "unknown"} title="Tests">
            {scan?.test_files.length
              ? `${scan.test_files.length} test files for agents to run and extend.`
              : scan
                ? "No tests found, so agents can't check their changes."
                : "Not scanned yet."}
          </Check>
          <Check state={scan?.docs.length ? "ok" : scan ? "warn" : "unknown"} title="Docs for agents">
            {scan?.docs.length
              ? `${scan.docs.length} docs, including ${scan.docs.slice(0, 2).join(" and ")}.`
              : scan
                ? "No README or agent instructions found."
                : "Not scanned yet."}
          </Check>
          <Check state={wiki?.status === "ready" && !wiki.dirty_since ? "ok" : wiki ? "warn" : "unknown"} title="Wiki">
            <WikiStatusPill status={wiki?.status ?? null} />{" "}
            {wiki?.last_indexed_at
              ? `Built ${fmtAgo(wiki.last_indexed_at)}${wiki.dirty_since ? `; out of date since ${wiki.dirty_reason ?? "a merge"}` : ""}.`
              : "Not built yet."}
          </Check>
        </ul>
      </section>
    </div>
  );
}
