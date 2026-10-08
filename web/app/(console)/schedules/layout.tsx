"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { fmtTime, statusLabel } from "@/lib/format";
import type { Schedule } from "@/lib/types";

/** The schedules list, kept mounted while the detail pane changes route. */
export default function SchedulesLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [schedules, setSchedules] = useState<Schedule[]>([]);

  const refresh = useCallback(async () => {
    try {
      setSchedules(await api.listSchedules());
    } catch {
      // API unreachable: keep the last list and retry on the next tick.
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  const match = pathname.match(/^\/schedules\/(\d+)/);
  const selectedId = match ? Number(match[1]) : null;

  return (
    <main>
      <section id="session-list-pane">
        <div className="list-pane-header">
          <Link href="/schedules/new" className="btn-primary btn-block">
            + New schedule
          </Link>
        </div>
        <div id="session-rows">
          {schedules.map((s) => (
            <Link key={s.id} href={`/schedules/${s.id}`}
                  className={"session-row skill-row" + (s.id === selectedId ? " selected" : "")}>
              <div className="session-row-top">
                <span className="session-row-title">{s.name}</span>
                {s.last_run && <span className={`status run-${s.last_run.status}`}>{statusLabel(s.last_run.status)}</span>}
                {!s.enabled && <span className="status status-closed">off</span>}
              </div>
              <div className="session-row-meta">
                <span>{s.repo ?? "no repo"}</span>
                <span className="sep">·</span>
                <span>{s.next_run_at ? `next ${fmtTime(s.next_run_at)}` : "not scheduled"}</span>
              </div>
            </Link>
          ))}
        </div>
        {schedules.length === 0 && (
          <div className="empty-state">No schedules yet. Add one to run a prompt on a recurring basis.</div>
        )}
      </section>
      <section id="detail-pane">{children}</section>
    </main>
  );
}
