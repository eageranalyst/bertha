import { useEffect, useState } from "react";

// Shared visual vocabulary for the transcript: a message always has a
// speaker (avatar + label), and user messages are visually unmistakable
// (right-aligned, accent color) -- the single strongest "who said this"
// cue, borrowed from ordinary chat apps. System notices are deliberately
// NOT messages -- no avatar, no bubble, just a quiet centered divider --
// since they're not something anyone "said."

export type Role = "user" | "agent" | "reviewer";

const AVATARS: Record<Role, string> = { user: "🧑", agent: "🤖", reviewer: "💬" };

export function MessageRow({
  role,
  label,
  time,
  children,
}: {
  role: Role;
  label: string;
  time: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`msg-row msg-${role}`}>
      <div className="msg-avatar">{AVATARS[role]}</div>
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
export function WorkingIndicator({ since, queued }: { since: number; queued: boolean }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);
  const elapsed = Math.max(0, Math.floor(now - since));
  return (
    <MessageRow role="agent" label="Agent" time={""}>
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
