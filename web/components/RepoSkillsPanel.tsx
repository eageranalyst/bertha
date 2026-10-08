"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { RepoSkill } from "@/lib/types";

/** The whole skills library, with a toggle for each skill on this repo. */
export function RepoSkillsPanel({ repoId }: { repoId: number }) {
  const [skills, setSkills] = useState<RepoSkill[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .listRepoSkills(repoId)
      .then((list) => alive && setSkills(list))
      .catch((err: Error) => alive && setError(err.message));
    return () => {
      alive = false;
    };
  }, [repoId]);

  async function toggle(skill: RepoSkill) {
    setError(null);
    try {
      setSkills(await api.setRepoSkill(repoId, skill.id, !skill.enabled));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (error && !skills) return <div className="empty-state">Could not load skills: {error}</div>;
  if (!skills) return <div className="empty-state">Loading…</div>;
  if (skills.length === 0) {
    return (
      <div className="empty-state">
        The library is empty. <Link href="/skills/new">Install a skill</Link> to enable it here.
      </div>
    );
  }

  const enabledCount = skills.filter((s) => s.enabled).length;

  return (
    <div className="skills-panel">
      <div className="skills-summary">
        {enabledCount} of {skills.length} enabled. Enabled skills are written into this repo&apos;s code
        sessions on their next turn.
      </div>
      {error && <div className="wiki-error">{error}</div>}
      <div className="skills-list">
        {skills.map((s) => (
          <div key={s.id} className={"skills-row" + (s.enabled ? " enabled" : "")}>
            <label className="skills-toggle" title={s.builtin ? "Built into Bertha: on for every repo" : undefined}>
              <input type="checkbox" checked={s.enabled} disabled={!!s.builtin} onChange={() => toggle(s)} />
            </label>
            <div className="skills-main">
              <div className="skills-top">
                <Link href={`/skills/${s.name}`} className="skills-name">
                  {s.name}
                </Link>
                {s.builtin ? <span className="skill-builtin">built in · always on</span> : null}
                <span className="skill-version">v{s.version}</span>
              </div>
              <div className="skill-description">{s.description}</div>
              {Object.keys(s.metadata).length > 0 && (
                <div className="skill-tags">
                  {Object.entries(s.metadata).map(([k, v]) => (
                    <span key={k} className="skill-tag">
                      {k}: {v}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
