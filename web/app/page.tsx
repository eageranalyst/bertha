"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { LogEntry, Repo, Session, SessionType } from "@/lib/types";
import { ComposeDraft } from "@/components/ComposeDraft";
import { SessionList } from "@/components/SessionList";
import { SessionDetail } from "@/components/SessionDetail";
import { ReposView } from "@/components/ReposView";

type SelectedId = number | "draft" | null;
type Tab = "sessions" | "repos";

const TABS: { id: Tab; label: string }[] = [
  { id: "sessions", label: "Sessions" },
  { id: "repos", label: "Repos" },
];

export default function Page() {
  const [tab, setTab] = useState<Tab>("sessions");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [selectedId, setSelectedId] = useState<SelectedId>(null);
  const [draftActive, setDraftActive] = useState(false);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);

  // These run on a timer. If the API is briefly unreachable (restarting,
  // or the laptop just woke), keep the last good list and let the next
  // tick retry, rather than leaving an unhandled rejection behind.
  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions());
    } catch {
      /* API unreachable; the next tick retries */
    }
  }, []);

  const refreshRepos = useCallback(async () => {
    try {
      setRepos(await api.listRepos());
    } catch {
      /* API unreachable; the next tick retries */
    }
  }, []);

  const refreshDetail = useCallback(async (id: number) => {
    try {
      const [session, logEntries] = await Promise.all([api.getSession(id), api.getLogs(id)]);
      setSelectedSession(session);
      setLogs(logEntries);
    } catch {
      setSelectedId(null);
      setSelectedSession(null);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
    refreshRepos();
    const sessionsInterval = setInterval(refreshSessions, 3000);
    // Repos carry their wiki status, which a running build changes.
    const reposInterval = setInterval(refreshRepos, 3000);
    return () => {
      clearInterval(sessionsInterval);
      clearInterval(reposInterval);
    };
  }, [refreshSessions, refreshRepos]);

  useEffect(() => {
    if (typeof selectedId !== "number") return;
    refreshDetail(selectedId);
    const interval = setInterval(() => refreshDetail(selectedId), 3000);
    return () => clearInterval(interval);
  }, [selectedId, refreshDetail]);

  function startDraft() {
    setDraftActive(true);
    setSelectedId("draft");
  }

  async function selectSession(id: number) {
    setDraftActive(false); // navigating away from any pending draft discards it
    setSelectedId(id);
    await refreshDetail(id);
  }

  async function submitDraft(sessionType: SessionType, repo: string | null, prompt: string | null) {
    try {
      const session = await api.createSession({ session_type: sessionType, repo, prompt });
      setDraftActive(false);
      await refreshSessions();
      await selectSession(session.id);
    } catch (err) {
      alert(`Could not start session: ${(err as Error).message}`);
    }
  }

  async function sendMessage(text: string) {
    if (typeof selectedId !== "number") return;
    await api.sendMessage(selectedId, text);
    await refreshDetail(selectedId);
  }

  async function withRefresh(action: () => Promise<unknown>) {
    if (typeof selectedId !== "number") return;
    await action();
    await refreshSessions();
    await refreshDetail(selectedId);
  }

  async function archiveSelected() {
    if (typeof selectedId !== "number") return;
    await api.archiveSession(selectedId);
    await refreshSessions();
    // Archived sessions drop out of the list (see visibleSessions below);
    // keeping the detail pane open on one would be showing something no
    // longer there, so close it too.
    setSelectedId(null);
    setSelectedSession(null);
  }

  const visibleSessions = sessions.filter((s) => s.status !== "archived");

  return (
    <div id="app">
      <header>
        <div className="brand">
          <span className="brand-mark">⚙</span>
          <div>
            <h1>Agent Factory</h1>
            <p className="subtitle">Local sessions, local model</p>
          </div>
        </div>
        <nav className="top-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={"top-tab" + (tab === t.id ? " active" : "")}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {tab === "repos" ? (
        <ReposView repos={repos} />
      ) : (
        <main>
          <SessionList
            sessions={visibleSessions}
            selectedId={selectedId}
            draftActive={draftActive}
            onNewSession={startDraft}
            onSelectDraft={() => setSelectedId("draft")}
            onSelectSession={selectSession}
          />

          <section id="detail-pane">
            {selectedId === "draft" && <ComposeDraft repos={repos} onSubmit={submitDraft} />}

            {typeof selectedId === "number" && selectedSession && (
              <SessionDetail
                session={selectedSession}
                logs={logs}
                onMessage={sendMessage}
                onStop={async () => {
                  if (typeof selectedId !== "number") return;
                  try {
                    await api.stopSession(selectedId);
                  } catch (err) {
                    alert(`Could not stop: ${(err as Error).message}`);
                  }
                  await refreshSessions();
                  await refreshDetail(selectedId);
                }}
                onClose={() => withRefresh(() => api.closeSession(selectedId as number))}
                onArchive={() => withRefresh(() => api.archiveSession(selectedId as number))}
                onReopen={() => withRefresh(() => api.reopenSession(selectedId as number))}
              />
            )}

            {selectedId === null && (
              <div className="empty-state">Select a session, or start a new one.</div>
            )}
          </section>
        </main>
      )}
    </div>
  );
}
