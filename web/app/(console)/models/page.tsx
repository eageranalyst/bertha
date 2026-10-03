"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Model } from "@/lib/types";

const TIER_HINT: Record<Model["tier"], string> = {
  light: "Quick, cheap work: summaries, simple edits, lookups.",
  standard: "Everyday coding and writing.",
  strong: "Hard tasks: multi-file changes, careful reasoning.",
};

export default function ModelsPage() {
  const [models, setModels] = useState<Model[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setModels(await api.listModels());
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      setModels(await api.refreshModels());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function update(key: string, patch: Partial<Pick<Model, "tier" | "description" | "enabled">>) {
    setError(null);
    try {
      await api.updateModel(key, patch);
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="models-page">
      <div className="models-header">
        <div>
          <h2 className="wiki-title">Models</h2>
          <p className="skill-hint">
            Local models the model server reports. A session runs on the model it was created with,
            and only enabled models can be chosen. The tier says what a model is for, and the
            description says what it&apos;s good at. Both are yours to set.
          </p>
        </div>
        <button type="button" className="btn-primary" disabled={busy} onClick={refresh}>
          Refresh from model server
        </button>
      </div>

      {error && <div className="wiki-error">{error}</div>}
      {models === null && !error && <div className="empty-state">Loading…</div>}
      {models?.length === 0 && (
        <div className="empty-state">
          No models yet. Start the model server and refresh.
        </div>
      )}

      {models && models.length > 0 && (
        <div className="models-list">
          {models.map((m) => (
            <div key={m.key} className={"models-row" + (m.enabled ? "" : " disabled")}>
              <label className="models-enabled">
                <input
                  type="checkbox"
                  checked={m.enabled}
                  onChange={(e) => update(m.key, { enabled: e.target.checked })}
                />
              </label>
              <div className="models-main">
                <div className="models-top">
                  <span className="models-key">{m.key}</span>
                  <span className="ref">{m.model_id}</span>
                </div>
                <input
                  className="models-description"
                  type="text"
                  defaultValue={m.description}
                  placeholder="What is this model good at?"
                  onBlur={(e) => {
                    if (e.target.value !== m.description) update(m.key, { description: e.target.value });
                  }}
                />
              </div>
              <div className="models-tier">
                <select value={m.tier} onChange={(e) => update(m.key, { tier: e.target.value as Model["tier"] })}>
                  {(Object.keys(TIER_HINT) as Model["tier"][]).map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                <span className="models-tier-hint">{TIER_HINT[m.tier]}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
