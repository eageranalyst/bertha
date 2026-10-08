"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ScheduleForm } from "@/components/ScheduleForm";
import { ApiError, api } from "@/lib/api";
import { fmtTime, statusLabel } from "@/lib/format";
import type { Schedule, ScheduleInput, ScheduleRun } from "@/lib/types";

function duration(run: ScheduleRun): string {
  if (!run.finished_at) return run.status === "running" ? "running" : "—";
  const seconds = Math.round(run.finished_at - run.started_at);
  return seconds < 90 ? `${seconds}s` : `${Math.round(seconds / 60)} min`;
}

export default function SchedulePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const scheduleId = Number(id);
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [runs, setRuns] = useState<ScheduleRun[]>([]);
  const [missing, setMissing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([api.getSchedule(scheduleId), api.listScheduleRuns(scheduleId)]);
      setSchedule(s);
      setRuns(r);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setMissing(true);
    }
  }, [scheduleId]);

  useEffect(() => {
    setSchedule(null);
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  if (missing) {
    return <div className="empty-state">That schedule doesn&apos;t exist. <Link href="/schedules">Back</Link></div>;
  }
  if (!schedule) return <div className="empty-state">Loading…</div>;

  async function save(value: ScheduleInput) {
    setSchedule(await api.updateSchedule(scheduleId, value));
    setNotice("Saved.");
  }

  async function runNow() {
    setError(null);
    try {
      const run = await api.runScheduleNow(scheduleId);
      setNotice(run.status === "running" ? "Started a run." : "The previous run is still going, so this one failed.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove() {
    if (!window.confirm(`Delete the schedule "${schedule?.name}"? Its past sessions stay in the Sessions list.`)) return;
    await api.deleteSchedule(scheduleId);
    router.push("/schedules");
  }

  const { last_run: _lastRun, id: _id, next_run_at: _next, created_at: _c, updated_at: _u, ...editable } = schedule;

  return (
    <div className="skill-page">
      <div id="detail-header">
        <div>
          <span className="pill pill-type-skill">schedule</span>
          <h2>{schedule.name}</h2>
        </div>
        <div id="detail-meta">
          {schedule.next_run_at ? `next run ${fmtTime(schedule.next_run_at)}` : "not scheduled"}
        </div>
      </div>
      <div id="detail-actions">
        <button type="button" className="btn-primary" onClick={runNow}>Run now</button>
        <button type="button" className="btn-danger" onClick={remove}>Delete</button>
        {notice && <span className="skill-form-hint">{notice}</span>}
      </div>
      {error && <div className="wiki-error">{error}</div>}

      <h3 className="meta-section">Runs</h3>
      {runs.length === 0 ? (
        <div className="empty-state">No runs yet.</div>
      ) : (
        <table className="runs-table">
          <thead>
            <tr><th>Scheduled for</th><th>Result</th><th>Took</th><th>Session</th><th>Note</th></tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{fmtTime(run.scheduled_for)}</td>
                <td><span className={`status run-${run.status}`}>{statusLabel(run.status)}</span></td>
                <td>{duration(run)}</td>
                <td>{run.session_ref ? <Link href={`/sessions/${run.session_ref}`}>{run.session_ref}</Link> : "—"}</td>
                <td className="runs-note">{run.reason ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 className="meta-section">Settings</h3>
      <ScheduleForm key={schedule.updated_at} initial={editable} saveLabel="Save changes" onSave={save} />
    </div>
  );
}
