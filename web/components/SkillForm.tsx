"use client";

import { useState } from "react";

/** Paste or pick a SKILL.md and submit it to install or update a skill. */
export function SkillForm({
  submitLabel,
  onSubmit,
}: {
  submitLabel: string;
  onSubmit: (content: string) => Promise<void>;
}) {
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function pickFile(file: File) {
    setContent(await file.text());
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await onSubmit(content);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="skill-form">
      <textarea
        className="skill-textarea"
        spellCheck={false}
        placeholder={"---\nname: my-skill\ndescription: What it does and when to use it\n---\n\n# My skill\n"}
        value={content}
        onChange={(e) => setContent(e.target.value)}
      />
      <div className="skill-form-actions">
        <label className="btn-ghost skill-file-pick">
          Choose a SKILL.md
          <input
            type="file"
            accept=".md,text/markdown,text/plain"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) pickFile(file);
              e.target.value = "";
            }}
          />
        </label>
        <button type="button" className="btn-primary" disabled={busy || !content.trim()} onClick={submit}>
          {submitLabel}
        </button>
      </div>
      {error && <div className="wiki-error">{error}</div>}
    </div>
  );
}
