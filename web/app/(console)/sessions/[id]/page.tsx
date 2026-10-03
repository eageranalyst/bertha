"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { SessionDetail } from "@/components/SessionDetail";
import { ApiError, api } from "@/lib/api";
import type { LogEntry, Session } from "@/lib/types";

export default function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const sessionId = Number(id);
  const [session, setSession] = useState<Session | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [missing, setMissing] = useState(false);

  const refresh = useCallback(async () => {
    if (!Number.isInteger(sessionId)) {
      setMissing(true);
      return;
    }
    try {
      const [s, l] = await Promise.all([api.getSession(sessionId), api.getLogs(sessionId)]);
      setSession(s);
      setLogs(l);
      setMissing(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setMissing(true);
      // Any other failure: keep what's on screen and retry on the next tick.
    }
  }, [sessionId]);

  useEffect(() => {
    setSession(null);
    setLogs([]);
    setMissing(false);
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [refresh]);

  async function run(action: () => Promise<unknown>) {
    try {
      await action();
    } catch (err) {
      alert((err as Error).message);
    }
    await refresh();
  }

  if (missing) {
    return (
      <div className="empty-state">
        That session doesn&apos;t exist. <Link href="/sessions">Back to sessions</Link>
      </div>
    );
  }
  if (!session) return <div className="empty-state">Loading…</div>;

  return (
    <SessionDetail
      session={session}
      logs={logs}
      onMessage={(text) => run(() => api.sendMessage(sessionId, text))}
      onStop={() => run(() => api.stopSession(sessionId))}
      onClose={() => run(() => api.closeSession(sessionId))}
      onArchive={async () => {
        try {
          await api.archiveSession(sessionId);
          router.push("/sessions");
        } catch (err) {
          alert((err as Error).message);
        }
      }}
      onReopen={() => run(() => api.reopenSession(sessionId))}
    />
  );
}
