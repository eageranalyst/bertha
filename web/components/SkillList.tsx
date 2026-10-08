"use client";

import Link from "next/link";
import type { Skill } from "@/lib/types";

export function SkillList({ skills, selectedName }: { skills: Skill[]; selectedName: string | null }) {
  return (
    <section id="session-list-pane">
      <div className="list-pane-header">
        <Link href="/skills/new" className="btn-primary btn-block">
          + Install skill
        </Link>
      </div>
      <div id="session-rows">
        {skills.map((s) => (
          <Link
            key={s.id}
            href={`/skills/${s.name}`}
            className={"session-row skill-row" + (s.name === selectedName ? " selected" : "")}
          >
            <div className="session-row-top">
              <span className="session-row-title">{s.name}</span>
              {s.builtin ? <span className="skill-builtin">built in</span> : null}
              <span className="skill-version">v{s.version}</span>
            </div>
            <div className="session-row-meta">
              <span className="skill-description">{s.description}</span>
            </div>
          </Link>
        ))}
      </div>
      {skills.length === 0 && (
        <div className="empty-state">No skills installed yet. Install a SKILL.md to get started.</div>
      )}
    </section>
  );
}
