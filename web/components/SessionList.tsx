"use client";

import { statusLabel } from "@/lib/format";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { AttentionReason, Session } from "@/lib/types";
import { QcMark } from "./images/QcMark";

const ATTENTION_LABEL: Record<AttentionReason, string> = {
  replied: "Waiting for your reply",
  review: "PR is open and waiting on you",
  spec: "Spec waiting on your review",
  failed: "The last turn failed",
};

/**
 * Top-level sessions, each with the QC review sessions of its PR, newest
 * first. A review whose session isn't in the list stays top-level.
 */
function groupReviews(sessions: Session[]): { session: Session; reviews: Session[] }[] {
  const ids = new Set(sessions.map((s) => s.id));
  const reviews = new Map<number, Session[]>();
  for (const s of sessions) {
    if (s.review_of != null && ids.has(s.review_of)) {
      reviews.set(s.review_of, [...(reviews.get(s.review_of) ?? []), s]);
    }
  }
  return sessions
    .filter((s) => s.review_of == null || !ids.has(s.review_of))
    .map((session) => ({
      session,
      reviews: (reviews.get(session.id) ?? []).sort((a, b) => b.created_at - a.created_at),
    }));
}

export function SessionList({
  sessions,
  selectedRef,
  draftActive,
  collapsed,
  onToggle,
}: {
  sessions: Session[];
  selectedRef: string | null;
  draftActive: boolean;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const router = useRouter();
  // Review groups opened by hand; a group also opens while one of its
  // reviews is open or running.
  const [opened, setOpened] = useState<Set<number>>(new Set());
  const groups = groupReviews(sessions);

  if (collapsed) {
    // A narrow rail: one dot per session, coloured by what it's waiting on.
    return (
      <section id="session-list-pane" className="collapsed">
        {/* Widens over the page on hover to show each session's name. */}
        <div className="rail-inner">
        <div className="list-rail-header">
          <button type="button" className="btn-ghost rail-toggle" onClick={onToggle}
                  title="Show the session list" aria-label="Show the session list">
            »
          </button>
          <Link href="/sessions/new" className="btn-primary rail-new" title="New session" aria-label="New session">
            +
          </Link>
        </div>
        <div className="session-rail">
          {groups.map(({ session: s, reviews }) => (
            <button
              key={s.id}
              type="button"
              className={
                "rail-item" + (s.ref === selectedRef || reviews.some((r) => r.ref === selectedRef) ? " selected" : "")
              }
              aria-label={`${s.title || "(untitled)"} · ${statusLabel(s.status)}${s.attention ? ` · ${ATTENTION_LABEL[s.attention]}` : ""}`}
              onClick={() => router.push(`/sessions/${s.ref}`)}
            >
              <span className={`rail-dot ${s.attention ? `attention-${s.attention}` : `rail-status-${s.status}`}`} />
              <span className="rail-label">
                <span className="rail-title">{s.title || "(untitled)"}</span>
                <span className={`status status-${s.status}`}>{statusLabel(s.status)}</span>
              </span>
            </button>
          ))}
        </div>
        </div>
      </section>
    );
  }

  return (
    <section id="session-list-pane">
      <div className="list-pane-header">
        <Link href="/sessions/new" className="btn-primary btn-block">
          + New session
        </Link>
        <button type="button" className="btn-ghost list-toggle" onClick={onToggle}
                title="Collapse the session list" aria-label="Collapse the session list">
          «
        </button>
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
        {groups.map(({ session: s, reviews }) => {
          const running = reviews.some((r) => r.status === "running" || r.status === "queued");
          const open = opened.has(s.id) || running || reviews.some((r) => r.ref === selectedRef);
          return (
            <div key={s.id} className="session-group">
              <div
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
                  <span className={`status status-${s.status}`}>{statusLabel(s.status)}</span>
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
              {reviews.length > 0 && (
                <button
                  type="button"
                  className="review-toggle"
                  aria-expanded={open}
                  onClick={() =>
                    setOpened((o) => {
                      const next = new Set(o);
                      if (next.has(s.id)) next.delete(s.id);
                      else next.add(s.id);
                      return next;
                    })
                  }
                >
                  <span className="review-toggle-caret" aria-hidden="true">
                    {open ? "▾" : "▸"}
                  </span>
                  <QcMark />
                  {reviews.length} QC review{reviews.length === 1 ? "" : "s"}
                  <span className="review-toggle-latest">· latest {statusLabel(reviews[0].status)}</span>
                </button>
              )}
              {open &&
                reviews.map((r, i) => (
                  <div
                    key={r.id}
                    className={"session-row session-child" + (r.ref === selectedRef ? " selected" : "")}
                    onClick={() => router.push(`/sessions/${r.ref}`)}
                  >
                    <QcMark />
                    <span className="session-row-title">Review {reviews.length - i}</span>
                    <span className="ref">{r.ref}</span>
                    <span className={`status status-${r.status}`}>{statusLabel(r.status)}</span>
                  </div>
                ))}
            </div>
          );
        })}
      </div>
      {sessions.length === 0 && !draftActive && (
        <div className="empty-state">No sessions yet — start one above.</div>
      )}
    </section>
  );
}
