"use client";

import { useMemo, useState } from "react";
import { checkSkill, fixSkill } from "@/lib/skillFormat";

/**
 * Edits a SKILL.md in place. Shows what the file is missing against the
 * skill requirements, can apply the mechanical fixes, and only saves when
 * there are no errors. `expectedName` pins the skill's name when editing.
 */
export function SkillEditor({
  initialContent,
  expectedName,
  saveLabel,
  onSave,
  onCancel,
}: {
  initialContent: string;
  expectedName?: string;
  saveLabel: string;
  onSave: (content: string) => Promise<void>;
  onCancel?: () => void;
}) {
  const [content, setContent] = useState(initialContent);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const issues = useMemo(() => checkSkill(content, expectedName), [content, expectedName]);
  const hasErrors = issues.some((i) => i.level === "error");
  const fixed = useMemo(() => fixSkill(content), [content]);
  const canFix = fixed !== content;
  const dirty = content !== initialContent;

  async function pickFile(file: File) {
    setContent(await file.text());
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave(content);
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

      {issues.length > 0 && (
        <ul className="skill-issues">
          {issues.map((issue, i) => (
            <li key={i} className={`skill-issue skill-issue-${issue.level}`}>
              <span className="skill-issue-level">{issue.level === "error" ? "Fix" : "Tip"}</span>
              {issue.message}
            </li>
          ))}
        </ul>
      )}

      <div className="skill-form-actions">
        <label className="btn-ghost skill-file-pick">
          Choose a file
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
        <button type="button" className="btn-ghost" disabled={!canFix} onClick={() => setContent(fixed)}>
          Fix formatting
        </button>
        {onCancel && (
          <button type="button" className="btn-ghost" onClick={onCancel}>
            Cancel
          </button>
        )}
        <button
          type="button"
          className="btn-primary"
          disabled={busy || hasErrors || !content.trim() || (expectedName !== undefined && !dirty)}
          onClick={save}
        >
          {saveLabel}
        </button>
        {dirty && <span className="skill-form-hint">Unsaved changes</span>}
        {hasErrors && <span className="skill-form-hint">Fix the errors above to save.</span>}
      </div>
      {error && <div className="wiki-error">{error}</div>}
    </div>
  );
}
