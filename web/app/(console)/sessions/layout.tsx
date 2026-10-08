"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { SessionList } from "@/components/SessionList";
import { api } from "@/lib/api";
import type { Session } from "@/lib/types";

const COLLAPSED_KEY = "bertha.sessionListCollapsed";

/** The sessions list, kept mounted while the detail pane changes route. */
export default function SessionsLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [collapsed, setCollapsed] = useState(false);

  // Remembered per browser; storage can be unavailable, which just means expanded.
  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSED_KEY) === "1");
    } catch {}
  }, []);

  function toggle() {
    setCollapsed((was) => {
      try {
        localStorage.setItem(COLLAPSED_KEY, was ? "0" : "1");
      } catch {}
      return !was;
    });
  }

  const refresh = useCallback(async () => {
    try {
      setSessions(await api.listSessions());
    } catch {
      // API unreachable: keep the last list and retry on the next tick.
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [refresh]);

  // /sessions/<ref>; "new" is the draft page, not a session.
  const match = pathname.match(/^\/sessions\/([^/]+)/);
  const selectedRef = match && match[1] !== "new" ? match[1] : null;
  const draftActive = pathname === "/sessions/new";

  return (
    <main>
      <SessionList
        sessions={sessions.filter((s) => s.status !== "archived")}
        selectedRef={selectedRef}
        draftActive={draftActive}
        collapsed={collapsed}
        onToggle={toggle}
      />
      <section id="detail-pane">{children}</section>
    </main>
  );
}
