"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { AttentionReason, Session } from "@/lib/types";

const ATTENTION_LABEL: Record<AttentionReason, string> = {
  replied: "Waiting for your reply",
  review: "PR is open and waiting on you",
  failed: "The last turn failed",
};

export function SessionList({
  sessions,
  selectedRef,
  draftActive,
}: {
  sessions: Session[];
  selectedRef: string | null;
  draftActive: boolean;
}) {
  const router = useRouter();

  return (
    <section id="session-list-pane">
      <div className="list-pane-header">
        <Link href="/sessions/new" className="btn-primary btn-block">
          + New session
        </Link>
      </div>
      <div id="session-rows">
        {draftActive && (
          <div className="session-row is-draft selected">
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
            className={"session-row" + (s.ref === selectedRef ? " selected" : "")}
            onClick={() => router.push(`/sessions/${s.ref}`)}
          >
            <div className="session-row-top">
              {s.attention && (
                <span
                  className={`attention-dot attention-${s.attention}`}
                  title={ATTENTION_LABEL[s.attention]}
                  role="img"
                  aria-label={ATTENTION_LABEL[s.attention]}
                />
              )}
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
