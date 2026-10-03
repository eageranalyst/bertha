"use client";

import { useState } from "react";

type Row = { key: string; value: string };

/** Key/value metadata for a skill, for organising the library later. */
export function SkillMetadataEditor({
  metadata,
  onSave,
}: {
  metadata: Record<string, string>;
  onSave: (metadata: Record<string, string>) => Promise<void>;
}) {
  const [rows, setRows] = useState<Row[]>(() =>
    Object.entries(metadata).map(([key, value]) => ({ key, value })),
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function update(index: number, patch: Partial<Row>) {
    setRows((rs) => rs.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  async function save() {
    const filled = rows.filter((r) => r.key.trim());
    const keys = filled.map((r) => r.key.trim());
    if (new Set(keys).size !== keys.length) {
      setError("Each key can only appear once.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSave(Object.fromEntries(filled.map((r) => [r.key.trim(), r.value])));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="meta-editor">
      {rows.map((row, i) => (
        <div key={i} className="meta-editor-row">
          <input
            type="text"
            placeholder="key"
            value={row.key}
            onChange={(e) => update(i, { key: e.target.value })}
          />
          <input
            type="text"
            placeholder="value"
            value={row.value}
            onChange={(e) => update(i, { value: e.target.value })}
          />
          <button
            type="button"
            className="btn-ghost"
            onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
            aria-label="Remove"
          >
            ×
          </button>
        </div>
      ))}
      <div className="meta-editor-actions">
        <button type="button" className="btn-ghost" onClick={() => setRows((rs) => [...rs, { key: "", value: "" }])}>
          Add field
        </button>
        <button type="button" className="btn-primary" disabled={busy} onClick={save}>
          Save metadata
        </button>
      </div>
      {error && <div className="wiki-error">{error}</div>}
    </div>
  );
}
