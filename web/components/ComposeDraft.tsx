"use client";

import { useState } from "react";
import type { Model, Repo, SessionType } from "@/lib/types";

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
  ) => Promise<void>;
}) {
  const [prompt, setPrompt] = useState("");
  const [repo, setRepo] = useState("");
  const [model, setModel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const enabled = models.filter((m) => m.enabled);

  async function submit(sessionType: SessionType) {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onSubmit(sessionType, repo || null, prompt.trim() || null, model || null);
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
          <option value="">Default</option>
          {enabled.map((m) => (
            <option key={m.key} value={m.key}>
              {m.key} ({m.tier})
            </option>
          ))}
        </select>
      </div>
      <div className="compose-actions">
        <button type="button" className="btn-primary" disabled={submitting} onClick={() => submit("ask")}>
          Ask
        </button>
        <button type="button" className="btn-primary btn-code" disabled={submitting} onClick={() => submit("code")}>
          Code
        </button>
      </div>
      <p className="compose-hint">Nothing is saved until you hit Ask or Code.</p>
    </div>
  );
}
