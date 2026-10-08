"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { GitHubCall, Session } from "@/lib/types";

const VERBS: Record<string, string> = {
  update_issue: "Edited",
  comment_issue: "Commented on",
  close_issue: "Closed",
  push_branch: "Pushed",
  create_pr: "Opened PR",
  update_pr: "Edited PR",
  comment_pr: "Commented on PR",
};

const PR_TOOLS = new Set(["push_branch", "create_pr", "update_pr", "comment_pr"]);

function target(call: GitHubCall) {
  if (call.issue_number == null) return null;
  const kind = PR_TOOLS.has(call.tool) ? "pull" : "issues";
  return (
    <a href={`https://github.com/${call.repo}/${kind}/${call.issue_number}`} target="_blank" rel="noreferrer">
      #{call.issue_number}
    </a>
  );
}

function describe(call: GitHubCall): string {
  const parts: string[] = [];
  if (call.tool === "push_branch" && call.after?.sha) parts.push(call.after.sha.slice(0, 7));
  if (call.tool === "create_pr" && call.after) parts.push(call.after.draft ? "draft" : "ready for review");
  if (call.tool === "update_pr" && call.before && call.after) {
    if (call.before.title !== call.after.title) parts.push("title");
    if (call.before.body !== call.after.body) parts.push("body");
    if (call.before.draft !== call.after.draft) parts.push(call.after.draft ? "back to draft" : "marked ready");
  }
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

/** What the agent changed on GitHub in this session, with an undo: a compact
 * bar above the message box that opens upwards, like a changed-files list. */
export function GitHubChanges({ session }: { session: Session }) {
  const [calls, setCalls] = useState<GitHubCall[]>([]);
  const [open, setOpen] = useState(false);
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
    setOpen(false);
    setMessage(null);
  }, [session.id]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  const writes = calls.filter((c) => c.tool in VERBS);
  if (writes.length === 0) return null;
  // Pushes can't be undone; everything else can.
  const undoable = writes.filter((c) => c.ok && !c.undone_at && c.tool !== "push_branch");
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

  const made = writes.filter((c) => c.ok).length;
  const refused = writes.length - made;

  return (
    <div className={"github-changes" + (open ? " open" : "")}>
      {open && (
        <ul>
          {writes.map((c) => (
            <li key={c.id} className={c.ok ? (c.undone_at ? "undone" : "") : "refused"}>
              {VERBS[c.tool]} {target(c)} {c.ok ? describe(c) : `(refused: ${c.error})`}
              {c.undone_at ? " · undone" : ""}
            </li>
          ))}
        </ul>
      )}
      <div className="github-changes-bar">
        <button type="button" className="github-changes-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="github-changes-chevron">▲</span>
          <span className="github-changes-count">
            {made} GitHub change{made === 1 ? "" : "s"}
          </span>
          {refused > 0 && <span className="github-changes-refused">{refused} refused</span>}
        </button>
        {message && <span className="skill-form-hint">{message}</span>}
        {undoable.length > 0 && (
          <button type="button" className="btn-ghost" disabled={busy || running} onClick={undo}
                  title={running ? "Stop the session first" : undefined}>
            Undo {undoable.length === made ? "all" : undoable.length}
          </button>
        )}
      </div>
    </div>
  );
}
