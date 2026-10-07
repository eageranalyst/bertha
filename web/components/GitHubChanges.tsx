"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { GitHubCall, Session } from "@/lib/types";

const VERBS: Record<string, string> = {
  update_issue: "Edited",
  comment_issue: "Commented on",
  close_issue: "Closed",
};

function describe(call: GitHubCall): string {
  const parts: string[] = [];
  if (call.tool === "update_issue" && call.before && call.after) {
    if (call.before.title !== call.after.title) parts.push("title");
    if (call.before.body !== call.after.body) parts.push("body");
    const added = (call.after.labels ?? []).filter((l) => !(call.before?.labels ?? []).includes(l));
    const removed = (call.before.labels ?? []).filter((l) => !(call.after?.labels ?? []).includes(l));
    if (added.length) parts.push(`+${added.join(", +")}`);
    if (removed.length) parts.push(`−${removed.join(", −")}`);
  }
  if (call.tool === "close_issue") {
    const reason = String(call.args.reason ?? "");
    parts.push(reason === "duplicate" ? `duplicate of #${call.args.duplicate_of}` : reason.replace("_", " "));
  }
  return parts.join(" · ");
}

/** What the agent changed on GitHub in this session, with an undo. */
export function GitHubChanges({ session }: { session: Session }) {
  const [calls, setCalls] = useState<GitHubCall[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setCalls(await api.listGitHubCalls(session.id));
    } catch {
      // Keep the last list; the next tick retries.
    }
  }, [session.id]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  const writes = calls.filter((c) => c.tool in VERBS);
  if (writes.length === 0) return null;
  const undoable = writes.filter((c) => c.ok && !c.undone_at);
  const running = session.status === "running" || session.status === "queued";

  async function undo() {
    if (!window.confirm(`Undo ${undoable.length} GitHub change(s) from this session?`)) return;
    setBusy(true);
    try {
      const results = await api.undoGitHubCalls(session.id);
      const failed = results.filter((r) => !r.undone);
      setMessage(failed.length ? `${failed.length} change(s) couldn't be undone.` : "Undone.");
      await refresh();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="github-changes" open>
      <summary>
        GitHub changes: {writes.filter((c) => c.ok).length} made
        {writes.some((c) => !c.ok) ? `, ${writes.filter((c) => !c.ok).length} refused` : ""}
      </summary>
      <ul>
        {writes.map((c) => (
          <li key={c.id} className={c.ok ? (c.undone_at ? "undone" : "") : "refused"}>
            <a href={`https://github.com/${c.repo}/issues/${c.issue_number}`} target="_blank" rel="noreferrer">
              #{c.issue_number}
            </a>{" "}
            {VERBS[c.tool]} {c.ok ? describe(c) : `(refused: ${c.error})`}
            {c.undone_at ? " · undone" : ""}
          </li>
        ))}
      </ul>
      {undoable.length > 0 && (
        <div className="github-changes-actions">
          <button type="button" className="btn-ghost" disabled={busy || running} onClick={undo}
                  title={running ? "Stop the session first" : undefined}>
            Undo {undoable.length} change{undoable.length === 1 ? "" : "s"}
          </button>
          {message && <span className="skill-form-hint">{message}</span>}
        </div>
      )}
    </details>
  );
}
