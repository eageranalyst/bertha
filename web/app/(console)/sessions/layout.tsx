"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { SessionList } from "@/components/SessionList";
import { api } from "@/lib/api";
import type { Session } from "@/lib/types";

/** The sessions list, kept mounted while the detail pane changes route. */
export default function SessionsLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [sessions, setSessions] = useState<Session[]>([]);

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
      />
      <section id="detail-pane">{children}</section>
    </main>
  );
}
