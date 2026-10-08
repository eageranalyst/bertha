"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { SessionDetail } from "@/components/SessionDetail";
import { ApiError, api } from "@/lib/api";
import type { LogEntry, Model, Session } from "@/lib/types";

/** Replaces entries that changed and adds new ones, keeping log order. */
function mergeLogs(current: LogEntry[], changed: LogEntry[]): LogEntry[] {
  const byId = new Map(current.map((l) => [l.id, l]));
  for (const l of changed) byId.set(l.id, l);
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

export default function SessionPage() {
  const { ref } = useParams<{ ref: string }>();
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [missing, setMissing] = useState(false);
  const [models, setModels] = useState<Model[]>([]);

  useEffect(() => {
    api.listModels().then(setModels).catch(() => {});
  }, []);

  const refresh = useCallback(async () => {
    try {
      const current = await api.getSessionByRef(ref);
      setSession(current);
      setLogs(await api.getLogs(current.id));
      setMissing(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setMissing(true);
      // Any other failure: keep what's on screen and retry on the next tick.
    }
  }, [ref]);

  useEffect(() => {
    setSession(null);
    setLogs([]);
    setMissing(false);
    refresh();
  }, [refresh]);

  // Live updates once the ref has resolved to a session. The browser
  // reconnects on its own after a drop, and each connection starts with a
  // full snapshot, so nothing is missed in between.
  const liveId = session?.id;
  useEffect(() => {
    if (liveId == null) return;
    const source = new EventSource(api.sessionEventsUrl(liveId));
    source.addEventListener("snapshot", (e) => {
      const data = JSON.parse((e as MessageEvent).data) as { session: Session | null; logs: LogEntry[] };
      if (data.session) setSession(data.session);
      setLogs(data.logs);
    });
    source.addEventListener("session", (e) => setSession(JSON.parse((e as MessageEvent).data)));
    source.addEventListener("logs", (e) => {
      const changed = JSON.parse((e as MessageEvent).data) as LogEntry[];
      setLogs((current) => mergeLogs(current, changed));
    });
    source.addEventListener("gone", () => {
      source.close();
      setMissing(true);
    });
    return () => source.close();
  }, [liveId]);

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

  const sessionId = session.id;

  return (
    <SessionDetail
      session={session}
      logs={logs}
      models={models}
      onModelChange={(model) => run(() => api.setSessionModel(sessionId, model))}
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
      onApproveSpec={() => run(() => api.approveSpec(sessionId))}
      onAbandonSpec={() => run(() => api.abandonSpec(sessionId))}
    />
  );
}
