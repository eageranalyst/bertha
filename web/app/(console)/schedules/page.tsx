import Link from "next/link";

export default function SchedulesIndex() {
  return (
    <div className="empty-state">
      Select a schedule, or <Link href="/schedules/new">add one</Link>. Each run starts a normal session, which
      appears in the Sessions list.
    </div>
  );
}
