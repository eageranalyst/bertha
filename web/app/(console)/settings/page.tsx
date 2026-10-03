"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AgentSetting } from "@/lib/types";

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<AgentSetting[] | null>(null);
  // Edits are kept as text so a half-typed number doesn't snap back.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api
      .listSettings()
      .then((list) => {
        setSettings(list);
        setDrafts(Object.fromEntries(list.map((s) => [s.key, String(s.value)])));
      })
      .catch((err) => setError(errorText(err)));
  }, []);

  async function save(values: Record<string, string>) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const list = await api.updateSettings(
        Object.fromEntries(Object.entries(values).map(([k, v]) => [k, Number(v)])),
      );
      setSettings(list);
      setDrafts(Object.fromEntries(list.map((s) => [s.key, String(s.value)])));
      setSaved(true);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (!settings) return <div className="empty-state">{error ?? "Loading…"}</div>;

  const dirty = settings.some((s) => drafts[s.key] !== String(s.value));

  return (
    <div className="settings-page">
      <h2 className="wiki-title">Agent limits</h2>
      <p className="skill-hint">
        These bound how far one code turn can go, so an agent can&apos;t loop or spend tokens without
        end. Changes apply to the next turn that starts.
      </p>

      {error && <div className="wiki-error">{error}</div>}

      <div className="settings-list">
        {settings.map((s) => (
          <div key={s.key} className="settings-row">
            <div className="settings-main">
              <label htmlFor={`setting-${s.key}`} className="settings-label">
                {s.label}
              </label>
              <div className="settings-help">{s.help}</div>
              <div className="settings-range">
                {s.minimum.toLocaleString()}–{s.maximum.toLocaleString()} · default{" "}
                {s.default.toLocaleString()}
              </div>
            </div>
            <input
              id={`setting-${s.key}`}
              type="number"
              inputMode="numeric"
              min={s.minimum}
              max={s.maximum}
              value={drafts[s.key] ?? ""}
              onChange={(e) => setDrafts((d) => ({ ...d, [s.key]: e.target.value }))}
            />
          </div>
        ))}
      </div>

      <div className="settings-actions">
        <button
          type="button"
          className="btn-primary"
          disabled={busy || !dirty}
          onClick={() => save(drafts)}
        >
          Save limits
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={busy}
          onClick={() => save(Object.fromEntries(settings.map((s) => [s.key, String(s.default)])))}
        >
          Reset to defaults
        </button>
        {saved && !dirty && <span className="skill-form-hint">Saved.</span>}
      </div>
    </div>
  );
}
