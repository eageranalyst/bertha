"use client";

import { useState } from "react";
import { modelLabel } from "@/lib/format";
import type { Model, Repo, SessionType } from "@/lib/types";

// A code task this long is usually big enough to want a spec reviewed first.
const SPEC_FIRST_PROMPT_CHARS = 600;

export function ComposeDraft({
  repos,
  models,
  onSubmit,
}: {
  repos: Repo[];
  models: Model[];
  onSubmit: (
    sessionType: SessionType,
    repo: string | null,
    prompt: string | null,
    model: string | null,
    specFirst: boolean,
    issueNumber: number | null,
  ) => Promise<void>;
}) {
  const [prompt, setPrompt] = useState("");
  const [repo, setRepo] = useState("");
  const [model, setModel] = useState("");
  const [issue, setIssue] = useState("");
  // null follows the prompt's length until the box is ticked or unticked.
  const [specChoice, setSpecChoice] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const enabled = models.filter((m) => m.usable);
  const specFirst = !!repo && (specChoice ?? prompt.trim().length >= SPEC_FIRST_PROMPT_CHARS);
  const codeLabel = specFirst ? "Spec" : "Code";

  async function submit(sessionType: SessionType) {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onSubmit(sessionType, repo || null, prompt.trim() || null, model || null,
                     sessionType === "code" && specFirst,
                     sessionType === "code" && repo && issue ? Number(issue) : null);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h2 className="compose-heading">New session</h2>
      <textarea
        id="compose-prompt"
        placeholder="What do you want to ask or build?"
        autoFocus
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
      />
      <div className="compose-row">
        <label htmlFor="compose-repo">Repo</label>
        <select id="compose-repo" value={repo} onChange={(e) => setRepo(e.target.value)}>
          <option value="">No repo</option>
          {repos.map((r) => (
            <option key={r.id} value={r.full_name}>
              {r.full_name}
            </option>
          ))}
        </select>
      </div>
      <div className="compose-row">
        <label htmlFor="compose-model">Model</label>
        <select id="compose-model" value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">Auto (routed per turn)</option>
          {enabled.map((m) => (
            <option key={m.key} value={m.key}>
              {modelLabel(m)}
            </option>
          ))}
        </select>
      </div>
      {repo && (
        <div className="compose-row">
          <label htmlFor="compose-issue">Issue</label>
          <input
            id="compose-issue"
            className="compose-issue"
            inputMode="numeric"
            placeholder="#"
            value={issue}
            onChange={(e) => setIssue(e.target.value.replace(/[^0-9]/g, ""))}
          />
          <span className="compose-hint-inline">
            Optional. The PR closes it, a spec is named after it, and its text is the task if you leave the box above
            empty.
          </span>
        </div>
      )}
      {repo && (
        <div className="compose-row">
          <label className="form-check">
            <input type="checkbox" checked={specFirst} onChange={(e) => setSpecChoice(e.target.checked)} />
            Spec first: write a spec on a draft PR for you to review, and build it once you approve it (here, or
            by commenting /approve-spec on the PR). On by default for longer tasks.
          </label>
        </div>
      )}
      <div className="compose-actions">
        <button type="button" className="btn-primary" disabled={submitting} onClick={() => submit("ask")}>
          Ask
        </button>
        <button type="button" className="btn-primary btn-code" disabled={submitting} onClick={() => submit("code")}>
          {codeLabel}
        </button>
      </div>
      <p className="compose-hint">Nothing is saved until you hit Ask or {codeLabel}.</p>
    </div>
  );
}
