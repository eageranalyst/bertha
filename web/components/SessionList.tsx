"use client";

import type { Session } from "@/lib/types";

export function SessionList({
  sessions,
  selectedId,
  draftActive,
  onNewSession,
  onSelectDraft,
  onSelectSession,
}: {
  sessions: Session[];
  selectedId: number | "draft" | null;
  draftActive: boolean;
  onNewSession: () => void;
  onSelectDraft: () => void;
  onSelectSession: (id: number) => void;
}) {
  return (
    <section id="session-list-pane">
      <div className="list-pane-header">
        <button type="button" className="btn-primary btn-block" onClick={onNewSession}>
          + New session
        </button>
      </div>
      <div id="session-rows">
        {draftActive && (
          <div
            className={"session-row is-draft" + (selectedId === "draft" ? " selected" : "")}
            onClick={onSelectDraft}
          >
            <div className="session-row-top">
              <span className="session-row-title">New session</span>
            </div>
            <div className="session-row-meta">
              <span className="ref">draft</span>
            </div>
          </div>
        )}
        {sessions.map((s) => (
          <div
            key={s.id}
            className={"session-row" + (s.id === selectedId ? " selected" : "")}
            onClick={() => onSelectSession(s.id)}
          >
            <div className="session-row-top">
              <span className={`pill pill-type-${s.session_type}`}>{s.session_type}</span>
              <span className="session-row-title">{s.title || "(untitled)"}</span>
              <span className={`status status-${s.status}`}>{s.status}</span>
            </div>
            <div className="session-row-meta">
              <span className="ref">{s.ref}</span>
              <span className="sep">·</span>
              <span>{s.repo ?? "no repo"}</span>
              {s.pr_number != null && (
                <>
                  <span className="sep">·</span>
                  <a
                    href={`https://github.com/${s.repo}/pull/${s.pr_number}`}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    PR #{s.pr_number}
                  </a>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
      {sessions.length === 0 && !draftActive && (
        <div className="empty-state">No sessions yet — start one above.</div>
      )}
    </section>
  );
}
