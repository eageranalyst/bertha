"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Model } from "@/lib/types";
import { SaveBar, useUnsavedWarning } from "@/components/SaveBar";

type Editable = Pick<Model, "tier" | "description" | "enabled">;
const FIELDS: (keyof Editable)[] = ["tier", "description", "enabled"];

function editable(m: Model): Editable {
  return { tier: m.tier, description: m.description, enabled: m.enabled };
}

const TIER_HINT: Record<Model["tier"], string> = {
  light: "Quick, cheap work: summaries, simple edits, lookups.",
  standard: "Everyday coding and writing.",
  strong: "Hard tasks: multi-file changes, careful reasoning.",
};

export default function ModelsPage() {
  const [models, setModels] = useState<Model[] | null>(null);
  // Your edits, by model key, applied when you save.
  const [drafts, setDrafts] = useState<Record<string, Editable>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const show = useCallback((list: Model[]) => {
    setModels(list);
    setDrafts(Object.fromEntries(list.map((m) => [m.key, editable(m)])));
  }, []);

  const load = useCallback(async () => {
    try {
      show(await api.listModels());
    } catch (err) {
      setError((err as Error).message);
    }
  }, [show]);

  useEffect(() => {
    load();
  }, [load]);

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      show(await api.refreshModels());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** What changed for each model: only the fields you edited. */
  const changes = (models ?? [])
    .map((m) => {
      const draft = drafts[m.key];
      const patch: Partial<Editable> = {};
      if (draft) for (const f of FIELDS) if (draft[f] !== m[f]) Object.assign(patch, { [f]: draft[f] });
      return [m.key, patch] as const;
    })
    .filter(([, patch]) => Object.keys(patch).length > 0);
  const dirty = changes.length > 0;
  useUnsavedWarning(dirty);

  function edit(key: string, patch: Partial<Editable>) {
    setDrafts((d) => ({ ...d, [key]: { ...d[key], ...patch } }));
  }

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      for (const [key, patch] of changes) await api.updateModel(key, patch);
      await load();
      setSaved(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const saveBar = (refreshButton: boolean) => (
    <SaveBar dirty={dirty} busy={busy} saved={saved} onSave={save} onDiscard={() => models && show(models)}>
      {refreshButton && (
        <button
          type="button"
          className="btn-ghost"
          disabled={busy || dirty}
          title={dirty ? "Save or discard your changes first" : undefined}
          onClick={refresh}
        >
          Refresh from model server
        </button>
      )}
    </SaveBar>
  );

  return (
    <div className="models-page">
      <p className="skill-hint">
        Local models the model server reports. A session runs on the model it was created with,
        and only enabled models can be chosen. The tier says what a model is for, and the
        description says what it&apos;s good at. Both are yours to set.
      </p>

      {saveBar(true)}
      {error && <div className="wiki-error">{error}</div>}
      {models === null && !error && <div className="empty-state">Loading…</div>}
      {models?.length === 0 && (
        <div className="empty-state">
          No models yet. Start the model server and refresh.
        </div>
      )}

      {models && models.length > 0 && (
        <div className="models-list">
          {models.map((model) => {
            const m = { ...model, ...drafts[model.key] };
            return (
            <div key={m.key} className={"models-row" + (m.enabled ? "" : " disabled")}>
              <label className="models-enabled">
                <input
                  type="checkbox"
                  checked={m.enabled}
                  onChange={(e) => edit(m.key, { enabled: e.target.checked })}
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
                  value={m.description}
                  placeholder="What is this model good at?"
                  onChange={(e) => edit(m.key, { description: e.target.value })}
                />
              </div>
              <div className="models-tier">
                <select value={m.tier} onChange={(e) => edit(m.key, { tier: e.target.value as Model["tier"] })}>
                  {(Object.keys(TIER_HINT) as Model["tier"][]).map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                <span className="models-tier-hint">{TIER_HINT[m.tier]}</span>
              </div>
            </div>
            );
          })}
        </div>
      )}
      {models && models.length > 0 && saveBar(false)}
    </div>
  );
}
