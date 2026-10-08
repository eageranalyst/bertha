"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ContextNote, ContextNoteStatus } from "@/lib/types";

const VIEWS: { status: ContextNoteStatus; label: string; empty: string }[] = [
  {
    status: "candidate",
    label: "To review",
    empty: "Nothing to review. Notes appear here when a Bertha pull request closes with review comments, or when Bertha fixes failing CI.",
  },
  { status: "saved", label: "Saved", empty: "No saved notes yet. Save a note from To review and agents get it." },
  { status: "rejected", label: "Rejected", empty: "Nothing rejected." },
];

const KIND_LABEL: Record<ContextNote["kind"], string> = { review: "Review", ci: "CI fix" };

/** A repo's agent context notes: the review queue, what's saved, and what was rejected. */
export function AgentContextPanel({ repo }: { repo: string }) {
  const [view, setView] = useState<ContextNoteStatus>("candidate");
  const [notes, setNotes] = useState<ContextNote[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setNotes(await api.listContextNotes(repo));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [repo]);

  // Notes are written in the background as PRs close and CI is fixed.
  useEffect(() => {
    load();
    const interval = setInterval(load, 10_000);
    return () => clearInterval(interval);
  }, [load]);

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const counts = Object.fromEntries(
    VIEWS.map((v) => [v.status, notes?.filter((n) => n.status === v.status).length ?? 0]),
  ) as Record<ContextNoteStatus, number>;
  const shown = notes?.filter((n) => n.status === view) ?? [];
  const current = VIEWS.find((v) => v.status === view)!;

  return (
    <div className="models-page">
      <p className="skill-hint">
        Lessons drawn from this repo&apos;s sessions: rules in reviewers&apos; comments on Bertha&apos;s pull
        requests, and the cause and fix of CI failures Bertha fixed. The local model writes each one up for
        you to review. Agents only see saved notes: their titles in every turn&apos;s context, and the full
        note when they read it with the <code>context_note</code> tool.
      </p>

      <div className="context-filter" role="tablist" aria-label="Notes">
        {VIEWS.map((v) => (
          <button
            key={v.status}
            type="button"
            role="tab"
            aria-selected={view === v.status}
            className={view === v.status ? "active" : ""}
            onClick={() => setView(v.status)}
          >
            {v.label} <span className="context-count">{counts[v.status]}</span>
          </button>
        ))}
      </div>

      {error && <div className="wiki-error">{error}</div>}
      {notes === null && !error && <div className="empty-state">Loading…</div>}
      {notes !== null && shown.length === 0 && <div className="empty-state">{current.empty}</div>}

      <div className="context-list">
        {shown.map((note) => (
          <NoteCard key={note.id} note={note} act={act} />
        ))}
      </div>
    </div>
  );
}

function NoteCard({
  note,
  act,
}: {
  note: ContextNote;
  act: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(note.title);
  const [body, setBody] = useState(note.body);
  const src = note.source;
  const setStatus = (status: ContextNoteStatus) => act(() => api.updateContextNote(note.id, { status }));

  return (
    <article className="context-card">
      <div className="context-vote" aria-label={`Score ${note.score}`}>
        <button type="button" title="More useful" onClick={() => act(() => api.voteContextNote(note.id, 1))}>
          ▲
        </button>
        <span className={note.score < 0 ? "negative" : ""}>{note.score}</span>
        <button type="button" title="Less useful" onClick={() => act(() => api.voteContextNote(note.id, -1))}>
          ▼
        </button>
      </div>

      <div className="context-main">
        <div className="context-meta">
          <span className={`pill pill-note-${note.kind}`}>{KIND_LABEL[note.kind]}</span>
          <span>{new Date(note.created_at * 1000).toLocaleDateString()}</span>
          {note.status === "saved" && <span>read by agents {note.uses}×</span>}
        </div>

        {editing ? (
          <div className="context-edit">
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title" />
            <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} aria-label="Note" />
            <div className="skill-form-actions">
              <button
                type="button"
                className="btn-primary"
                onClick={() => act(() => api.updateContextNote(note.id, { title, body })).then(() => setEditing(false))}
              >
                Save changes
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => {
                  setTitle(note.title);
                  setBody(note.body);
                  setEditing(false);
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            <h3 className="context-title">{note.title}</h3>
            <p className="context-body">{note.body}</p>
          </>
        )}

        <details className="context-source">
          <summary>
            From{" "}
            {src.pr_number != null ? `${note.kind === "review" ? "the review of" : "a CI fix on"} PR #${src.pr_number}` : "a session"}
            {src.session_title ? `: ${src.session_title}` : ""}
          </summary>
          {note.quote && <blockquote>{note.quote}</blockquote>}
          <ul>
            {src.session_ref && (
              <li>
                Session <Link href={`/sessions/${src.session_ref}`}>{src.session_ref}</Link>
              </li>
            )}
            {src.pr_url && (
              <li>
                <a href={src.pr_url} target="_blank" rel="noreferrer">
                  PR #{src.pr_number}
                </a>
                {src.merged != null && ` (${src.merged ? "merged" : "closed without merging"})`}
              </li>
            )}
            {src.comments?.map((c, i) => (
              <li key={i}>
                <a href={c.url} target="_blank" rel="noreferrer">
                  @{c.author}
                  {c.path ? ` on ${c.path}${c.line ? `:${c.line}` : ""}` : "'s comment"}
                </a>
              </li>
            ))}
            {src.jobs && src.jobs.length > 0 && <li>Failed: {src.jobs.join(", ")}</li>}
            {src.compare_url && (
              <li>
                <a href={src.compare_url} target="_blank" rel="noreferrer">
                  The fix ({src.failed_sha?.slice(0, 7)}…{src.fixed_sha?.slice(0, 7)})
                </a>
              </li>
            )}
          </ul>
        </details>

        {!editing && (
          <div className="context-actions">
            {note.status === "candidate" && (
              <>
                <button type="button" className="btn-primary" onClick={() => setStatus("saved")}>
                  Save to memory
                </button>
                <button type="button" className="btn-ghost" onClick={() => setStatus("rejected")}>
                  Reject
                </button>
              </>
            )}
            {note.status === "saved" && (
              <button type="button" className="btn-ghost" onClick={() => setStatus("rejected")}>
                Remove from memory
              </button>
            )}
            {note.status === "rejected" && (
              <button type="button" className="btn-ghost" onClick={() => setStatus("candidate")}>
                Back to review
              </button>
            )}
            <button type="button" className="btn-ghost" onClick={() => setEditing(true)}>
              Edit
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
