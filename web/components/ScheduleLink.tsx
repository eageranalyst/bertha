"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** "Run of <schedule name>", linking to the schedule that started a session. */
export function ScheduleLink({ scheduleId }: { scheduleId: number }) {
  const [name, setName] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .getSchedule(scheduleId)
      .then((s) => alive && setName(s.name))
      .catch(() => alive && setMissing(true));
    return () => {
      alive = false;
    };
  }, [scheduleId]);

  if (missing) return <span className="schedule-link">Run of a deleted schedule</span>;
  return (
    <span className="schedule-link">
      Run of <Link href={`/schedules/${scheduleId}`}>{name ?? "a schedule"}</Link>
    </span>
  );
}
