import { useEffect, useState, type ReactNode } from "react";
import { FactoryLogo } from "./images/FactoryMascot";
import { QcMark } from "./images/QcMark";

// Shared visual vocabulary for the transcript: a message always has a
// speaker (avatar + label), and user messages are visually unmistakable
// (right-aligned, accent color) -- the single strongest "who said this"
// cue, borrowed from ordinary chat apps. System notices are deliberately
// NOT messages -- no avatar, no bubble, just a quiet centered divider --
// since they're not something anyone "said."

export type Role = "user" | "agent" | "reviewer" | "qc";

// The agent is Bertha, so it speaks with her face: the header logo.
const AVATARS: Record<Role, ReactNode> = { user: "🧑", agent: <FactoryLogo />, reviewer: "💬", qc: <QcMark /> };

export function MessageRow({
  role,
  label,
  time,
  avatar,
  children,
}: {
  role: Role;
  label: string;
  time: string;
  /** Whose avatar to show, when it isn't the role's own: Bertha, giving QC its task. */
  avatar?: Role;
  children: React.ReactNode;
}) {
  const face = avatar ?? role;
  return (
    <div className={`msg-row msg-${role}`}>
      <div className={`msg-avatar msg-avatar-${face}`}>{AVATARS[face]}</div>
      <div className="msg-main">
        <div className="msg-meta">
          <span className="msg-label">{label}</span>
          <span className="msg-time">{time}</span>
        </div>
        {children}
      </div>
    </div>
  );
}

export function MessageBubble({ children }: { children: React.ReactNode }) {
  return <div className="msg-bubble">{children}</div>;
}

export function SystemDivider({ time, content }: { time: string; content: string }) {
  return (
    <div className="sys-divider">
      <span>{content}</span>
      <span className="sys-time">{time}</span>
    </div>
  );
}

// Shown while a turn is queued or running, so submitting never looks
// like nothing happened. `since` is unix seconds of when the work began.
export function WorkingIndicator({ since, queued, qc = false }: { since: number; queued: boolean; qc?: boolean }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);
  const elapsed = Math.max(0, Math.floor(now - since));
  return (
    <MessageRow role={qc ? "qc" : "agent"} label={qc ? "QC" : "Bertha"} time={""}>
      <div className="msg-bubble working">
        <span className="working-dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        {queued ? "Queued" : "Working"} · {elapsed}s
      </div>
    </MessageRow>
  );
}
