"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { CRON_PRESETS, SCHEDULE_TEMPLATES } from "@/lib/scheduleTemplates";
import type { Model, Repo, ScheduleInput } from "@/lib/types";

/** The browser's time zone: schedules are entered and shown in local time. */
export function localTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function emptySchedule(): ScheduleInput {
  return {
    name: "",
    repo: null,
    prompt: "",
    cron: "0 2 * * *",
    timezone: localTimeZone(),
    timeout_minutes: 60,
    model: null,
    session_type: "code",
    github_tools: false,
    enabled: true,
  };
}

export function ScheduleForm({
  initial,
  saveLabel,
  onSave,
}: {
  initial: ScheduleInput;
  saveLabel: string;
  onSave: (value: ScheduleInput) => Promise<void>;
}) {
  const [value, setValue] = useState<ScheduleInput>(initial);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [preview, setPreview] = useState<number[] | null>(null);
  const [cronError, setCronError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listRepos().then(setRepos).catch(() => {});
    api.listModels().then(setModels).catch(() => {});
  }, []);

  // Preview the next runs as the cron is typed.
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      api
        .previewCron(value.cron, value.timezone)
        .then((r) => {
          if (!alive) return;
          setPreview(r.next);
          setCronError(null);
        })
        .catch((err: Error) => {
          if (!alive) return;
          setPreview(null);
          setCronError(err.message);
        });
    }, 300);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [value.cron, value.timezone]);

  function set<K extends keyof ScheduleInput>(key: K, next: ScheduleInput[K]) {
    setValue((v) => ({ ...v, [key]: next }));
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave(value);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const template = SCHEDULE_TEMPLATES[0];

  return (
    <div className="schedule-form">
      <div className="form-row">
        <label htmlFor="sched-name">Name</label>
        <input id="sched-name" type="text" value={value.name} onChange={(e) => set("name", e.target.value)}
               placeholder="Nightly backlog groom" />
      </div>

      <div className="form-row">
        <label htmlFor="sched-repo">Repo</label>
        <select id="sched-repo" value={value.repo ?? ""} onChange={(e) => {
          const repo = e.target.value || null;
          setValue((v) => ({ ...v, repo, github_tools: repo ? v.github_tools : false }));
        }}>
          <option value="">No repo</option>
          {repos.map((r) => <option key={r.id} value={r.full_name}>{r.full_name}</option>)}
        </select>
      </div>

      <div className="form-row form-row-top">
        <label htmlFor="sched-prompt">Prompt</label>
        <div className="form-stack">
          <textarea id="sched-prompt" className="skill-textarea" value={value.prompt}
                    onChange={(e) => set("prompt", e.target.value)}
                    placeholder="What should the agent do on each run?" />
          <div>
            <button type="button" className="btn-ghost" onClick={() => setValue((v) => ({
              ...v,
              prompt: template.prompt,
              name: v.name || template.name,
              github_tools: Boolean(v.repo),
              session_type: "code",
            }))}>
              Use the {template.label} template
            </button>
          </div>
        </div>
      </div>

      <div className="form-row form-row-top">
        <label htmlFor="sched-cron">Schedule</label>
        <div className="form-stack">
          <div className="cron-presets">
            {CRON_PRESETS.map((p) => (
              <button key={p.cron} type="button"
                      className={"btn-ghost" + (value.cron === p.cron ? " preset-active" : "")}
                      onClick={() => set("cron", p.cron)}>
                {p.label}
              </button>
            ))}
          </div>
          <input id="sched-cron" type="text" className="cron-input" value={value.cron}
                 onChange={(e) => set("cron", e.target.value)} spellCheck={false} />
          <div className="settings-help">
            Cron: minute hour day-of-month month day-of-week, in {value.timezone}.
          </div>
          {cronError ? (
            <div className="wiki-error">{cronError}</div>
          ) : preview ? (
            <div className="cron-preview">
              Next runs: {preview.map((t) => fmtTime(t)).join(" · ")}
            </div>
          ) : null}
        </div>
      </div>

      <div className="form-row">
        <label htmlFor="sched-timeout">Timeout</label>
        <div className="form-inline">
          <input id="sched-timeout" type="number" min={1} max={1440} value={value.timeout_minutes}
                 onChange={(e) => set("timeout_minutes", Number(e.target.value))} />
          <span className="settings-help">minutes. A run past this is stopped, and a run that starts while the
            previous one is still going fails instead.</span>
        </div>
      </div>

      <div className="form-row">
        <label htmlFor="sched-type">Session</label>
        <select id="sched-type" value={value.session_type}
                onChange={(e) => set("session_type", e.target.value as ScheduleInput["session_type"])}>
          <option value="code">Code (repo checked out)</option>
          <option value="ask">Ask (no container)</option>
        </select>
      </div>

      <div className="form-row">
        <label htmlFor="sched-model">Model</label>
        <select id="sched-model" value={value.model ?? ""} onChange={(e) => set("model", e.target.value || null)}>
          <option value="">Auto (routed per turn)</option>
          {models.filter((m) => m.enabled).map((m) => (
            <option key={m.key} value={m.key}>{m.key} ({m.tier})</option>
          ))}
        </select>
      </div>

      <div className="form-row">
        <span className="form-label">GitHub</span>
        <label className="form-check">
          <input type="checkbox" checked={value.github_tools} disabled={!value.repo || value.session_type !== "code"}
                 onChange={(e) => set("github_tools", e.target.checked)} />
          Let the agent change and close any issue in this repo (labels, bodies, comments, closing), not just its
          own. Every session can already read issues and change its own issue and pull request. Changes apply
          directly, within the per-turn limits in Settings, and can be undone from the session.
        </label>
      </div>

      <div className="form-row">
        <span className="form-label">Enabled</span>
        <label className="form-check">
          <input type="checkbox" checked={value.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          Run on this schedule
        </label>
      </div>

      {error && <div className="wiki-error">{error}</div>}
      <div className="settings-actions">
        <button type="button" className="btn-primary" disabled={busy || Boolean(cronError)} onClick={save}>
          {saveLabel}
        </button>
      </div>
    </div>
  );
}
