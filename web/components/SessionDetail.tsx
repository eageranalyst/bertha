"use client";

import { useEffect, useRef, useState } from "react";
import type { LogEntry, Model, Session } from "@/lib/types";
import { Markdown } from "./Markdown";
import { AgentOutputLog } from "./AgentOutputLog";
import { GitHubChanges } from "./GitHubChanges";
import { ScheduleLink } from "./ScheduleLink";
import { SpecPanel } from "./SpecPanel";
import {
  MessageRow,
  MessageBubble,
  SystemDivider,
  WorkingIndicator,
  type Role,
} from "./ChatPrimitives";

function fmtTime(ts: number | null | undefined) {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString();
}

function speakerFor(kind: string, content: string): { role: Role; label: string } {
  if (kind === "user_message") return { role: "user", label: "You" };
  if (kind === "ask_response") return { role: "agent", label: "Agent" };
  if (kind === "ci_failure") return { role: "reviewer", label: "CI failed" };
  if (kind === "pr_comment") {
    const author = content.match(/^@(\S+):/)?.[1];
    return { role: "reviewer", label: author ? `@${author}` : "PR comment" };
  }
  return { role: "agent", label: "Agent" };
}

export function SessionDetail({
  session,
  logs,
  models,
  onMessage,
  onStop,
  onClose,
  onArchive,
  onReopen,
  onModelChange,
  onApproveSpec,
  onAbandonSpec,
}: {
  session: Session;
  logs: LogEntry[];
  models: Model[];
  onMessage: (text: string) => Promise<void>;
  onStop: () => Promise<void>;
  onClose: () => Promise<void>;
  onArchive: () => Promise<void>;
  onReopen: () => Promise<void>;
  /** null hands the session back to per-turn routing. */
  onModelChange: (model: string | null) => Promise<void>;
  onApproveSpec: () => Promise<void>;
  onAbandonSpec: () => Promise<void>;
}) {
  const pinned = session.model_pinned === 1;
  const enabledModels = models.filter((m) => m.enabled);
  // A pinned model that has since been disabled still shows, so the select stays honest.
  const modelOptions = [
    ...enabledModels,
    ...(pinned && session.model && !enabledModels.some((m) => m.key === session.model)
      ? [{ key: session.model, tier: "standard" as const }]
      : []),
  ];
  const [text, setText] = useState("");
  // Reopen only applies to a session that's been closed or archived.
  const isClosed = session.status === "closed" || session.status === "archived";
  // The message you just sent, shown immediately (before the server
  // has logged it). Dropped once the matching log entry arrives.
  const [pendingEcho, setPendingEcho] = useState<{ text: string; at: number } | null>(null);
  const logsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setPendingEcho(null);
  }, [session.id]);

  useEffect(() => {
    if (!pendingEcho) return;
    const confirmed = logs.some(
      (l) =>
        l.kind === "user_message" &&
        l.content === pendingEcho.text &&
        l.created_at >= pendingEcho.at - 5
    );
    if (confirmed) setPendingEcho(null);
  }, [logs, pendingEcho]);

  const lastUserMessage = [...logs].reverse().find((l) => l.kind === "user_message");
  const workingSince =
    pendingEcho?.at ?? lastUserMessage?.created_at ?? session.last_activity_at;
  // Set when the user sends a message, so the transcript scrolls to it
  // even if they'd scrolled up to read earlier output.
  const forceScrollRef = useRef(false);

  // Opening a session should land on its latest output, not the top.
  useEffect(() => {
    forceScrollRef.current = true;
  }, [session.id]);

  useEffect(() => {
    const el = logsRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    if (atBottom || forceScrollRef.current) {
      el.scrollTop = el.scrollHeight;
      forceScrollRef.current = false;
    }
  }, [logs]);

  async function submit() {
    const trimmed = text.trim();
    if (!trimmed) return;
    setText("");
    forceScrollRef.current = true;
    setPendingEcho({ text: trimmed, at: Date.now() / 1000 });
    await onMessage(trimmed);
  }

  return (
    <>
      <div id="detail-header">
        <div>
          <span className={`pill pill-type-${session.session_type}`}>{session.session_type}</span>{" "}
          <span className="ref">{session.ref}</span>
          <h2>{session.title || "(untitled)"}</h2>
          {session.schedule_id != null && <ScheduleLink scheduleId={session.schedule_id} />}
        </div>
        <div id="detail-meta">
          <span className={`status status-${session.status}`}>{session.status}</span>
          {session.repo ? ` · ${session.repo}` : " · no repo"}
          {session.pr_number != null ? ` · PR #${session.pr_number}` : ""}
          {` · ${pinned ? `${session.model} (pinned)` : `auto: ${session.model ?? "default"}`}`}
          {` · ${session.turn_count} turn${session.turn_count === 1 ? "" : "s"}`}
          {` · ${fmtTime(session.last_activity_at)}`}
        </div>
      </div>

      <div id="detail-actions">
        <label className="model-select">
          <span>Model</span>
          <select
            value={pinned && session.model ? session.model : "auto"}
            onChange={(e) => onModelChange(e.target.value === "auto" ? null : e.target.value)}
          >
            <option value="auto">Auto (routed per turn)</option>
            {modelOptions.map((m) => (
              <option key={m.key} value={m.key}>
                {m.key} ({m.tier})
              </option>
            ))}
          </select>
        </label>
        {session.status === "running" && (
          <button className="btn-danger" onClick={onStop}>
            Stop
          </button>
        )}
        {!isClosed && (
          <button className="btn-ghost" onClick={onClose}>
            Close
          </button>
        )}
        {session.status !== "archived" && (
          <button className="btn-ghost" onClick={onArchive}>
            Archive
          </button>
        )}
        {isClosed && (
          <button className="btn-ghost" onClick={onReopen}>
            Reopen
          </button>
        )}
      </div>

      {session.spec_phase && <SpecPanel session={session} onApprove={onApproveSpec} onAbandon={onAbandonSpec} />}

      <div id="detail-logs" ref={logsRef}>
        {logs.map((l) => {
          const time = new Date(l.created_at * 1000).toLocaleTimeString();
          if (l.kind === "system") {
            return <SystemDivider key={l.id} time={time} content={l.content} />;
          }
          if (l.kind === "agent_output") {
            return <AgentOutputLog key={l.id} content={l.content} time={time} />;
          }
          const { role, label } = speakerFor(l.kind, l.content);
          return (
            <MessageRow key={l.id} role={role} label={label} time={time}>
              <MessageBubble>
                <Markdown>{l.content}</Markdown>
              </MessageBubble>
            </MessageRow>
          );
        })}
        {pendingEcho && (
          <MessageRow role="user" label="You" time="sending…">
            <MessageBubble>
              <div className="pending-echo">{pendingEcho.text}</div>
            </MessageBubble>
          </MessageRow>
        )}
        {(session.status === "running" || session.status === "queued") && (
          <WorkingIndicator since={workingSince} queued={session.status === "queued"} />
        )}
      </div>

      {session.repo && <GitHubChanges session={session} />}

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
