"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Markdown } from "@/components/Markdown";
import { SkillForm } from "@/components/SkillForm";
import { SkillMetadataEditor } from "@/components/SkillMetadataEditor";
import { ApiError, api } from "@/lib/api";
import { fmtTime, splitFrontmatter } from "@/lib/format";
import type { SkillWithContent } from "@/lib/types";

export default function SkillPage() {
  const { name } = useParams<{ name: string }>();
  const router = useRouter();
  const [skill, setSkill] = useState<SkillWithContent | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSkill(await api.getSkillByName(name));
      setMissing(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setMissing(true);
    }
  }, [name]);

  useEffect(() => {
    setSkill(null);
    refresh();
  }, [refresh]);

  if (missing) {
    return (
      <div className="empty-state">
        No skill called {name}. <Link href="/skills">Back to skills</Link>
      </div>
    );
  }
  if (!skill) return <div className="empty-state">Loading…</div>;

  const { frontmatter, body } = splitFrontmatter(skill.content);

  async function saveMetadata(metadata: Record<string, string>) {
    if (!skill) return;
    setSkill(await api.setSkillMetadata(skill.id, metadata));
  }

  // Replacing the markdown bumps the version. If the new frontmatter names a
  // different skill, that's a separate skill, so go to it.
  async function replace(content: string) {
    const saved = await api.saveSkill(content);
    if (saved.name !== name) {
      router.push(`/skills/${saved.name}`);
      return;
    }
    setError(null);
    await refresh();
  }

  async function remove() {
    if (!skill || !window.confirm(`Delete the skill ${skill.name}? Repos lose it on their next turn.`)) return;
    try {
      await api.deleteSkill(skill.id);
      router.push("/skills");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="skill-page">
      <div id="detail-header">
        <div>
          <span className="pill pill-type-skill">skill</span>
          <h2>{skill.name}</h2>
          <p className="skill-description-lg">{skill.description}</p>
        </div>
        <div id="detail-meta">
          v{skill.version} · updated {fmtTime(skill.updated_at)}
        </div>
      </div>

      {error && <div className="wiki-error">{error}</div>}

      <h3 className="meta-section">Metadata</h3>
      <SkillMetadataEditor key={skill.id} metadata={skill.metadata} onSave={saveMetadata} />

      <h3 className="meta-section">Content</h3>
      <div className="skill-body">
        {frontmatter !== null && (
          <details className="skill-frontmatter" open>
            <summary>Frontmatter</summary>
            <pre>{frontmatter}</pre>
          </details>
        )}
        <Markdown>{body}</Markdown>
      </div>

      <details className="skill-replace">
        <summary>Replace the SKILL.md (bumps the version)</summary>
        <SkillForm submitLabel="Save new version" onSubmit={replace} />
      </details>

      <div className="skill-danger">
        <button type="button" className="btn-danger" onClick={remove}>
          Delete skill
        </button>
      </div>
    </div>
  );
}
