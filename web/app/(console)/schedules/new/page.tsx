"use client";

import { useRouter } from "next/navigation";
import { ScheduleForm, emptySchedule } from "@/components/ScheduleForm";
import { api } from "@/lib/api";
import type { ScheduleInput } from "@/lib/types";

export default function NewSchedulePage() {
  const router = useRouter();

  async function create(value: ScheduleInput) {
    const schedule = await api.createSchedule(value);
    router.push(`/schedules/${schedule.id}`);
  }

  return (
    <div className="skill-page">
      <h2 className="wiki-title">New schedule</h2>
      <p className="skill-hint">
        A prompt that runs on a schedule. Each run starts a normal session with this prompt; it shows in the
        Sessions list, and in this schedule&apos;s run history.
      </p>
      <ScheduleForm initial={emptySchedule()} saveLabel="Create schedule" onSave={create} />
    </div>
  );
}
