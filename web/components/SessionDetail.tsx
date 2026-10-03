"use client";

import { useEffect, useRef, useState } from "react";
import type { LogEntry, Session } from "@/lib/types";
import { Markdown } from "./Markdown";

const MARKDOWN_KINDS = new Set(["ask_response", "user_message", "pr_comment"]);

function fmtTime(ts: number | null | undefined) {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString();
}

export function SessionDetail({
  session,
  logs,
  onMessage,
  onStop,
  onClose,
  onArchive,
  onReopen,
}: {
  session: Session;
  logs: LogEntry[];
  onMessage: (text: string) => Promise<void>;
  onStop: () => Promise<void>;
  onClose: () => Promise<void>;
  onArchive: () => Promise<void>;
  onReopen: () => Promise<void>;
}) {
  const [text, setText] = useState("");
  const logsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = logsRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [logs]);

  async function submit() {
    const trimmed = text.trim();
    if (!trimmed) return;
    setText("");
    await onMessage(trimmed);
  }

  return (
    <>
      <div id="detail-header">
        <div>
          <span className={`pill pill-type-${session.session_type}`}>{session.session_type}</span>{" "}
          <span className="ref">{session.ref}</span>
          <h2>{session.title || "(untitled)"}</h2>
        </div>
        <div id="detail-meta">
          <span className={`status status-${session.status}`}>{session.status}</span>
          {session.repo ? ` · ${session.repo}` : " · no repo"}
          {session.pr_number != null ? ` · PR #${session.pr_number}` : ""}
          {` · ${session.turn_count} turn${session.turn_count === 1 ? "" : "s"}`}
          {` · ${fmtTime(session.last_activity_at)}`}
        </div>
      </div>

      <div id="detail-actions">
        {session.status === "running" && (
          <button className="btn-danger" onClick={onStop}>
            Stop
          </button>
        )}
        <button className="btn-ghost" onClick={onClose}>
          Close
        </button>
        <button className="btn-ghost" onClick={onArchive}>
          Archive
        </button>
        <button className="btn-ghost" onClick={onReopen}>
          Reopen
        </button>
      </div>

      <div id="detail-logs" ref={logsRef}>
        {logs.map((l) => (
          <div className="log-line" key={l.id}>
            <span className="log-kind">
              [{new Date(l.created_at * 1000).toLocaleTimeString()}] ({l.kind})
            </span>
            {MARKDOWN_KINDS.has(l.kind) ? (
              <Markdown>{l.content}</Markdown>
            ) : (
              <div className="log-body" style={{ whiteSpace: "pre-wrap" }}>
                {l.content}
              </div>
            )}
          </div>
        ))}
      </div>

      <form
        id="message-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          id="message-text"
          placeholder="Message the agent…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button type="submit" className="btn-primary">
          Send
        </button>
      </form>
    </>
  );
}
