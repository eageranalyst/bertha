import type {
  LogEntry,
  Repo,
  RepoMetadata,
  Session,
  SessionType,
  WikiPage,
  WikiResponse,
} from "./types";

const API_ORIGIN = process.env.NEXT_PUBLIC_API_ORIGIN ?? "http://127.0.0.1:8787";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_ORIGIN}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${await res.text()}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listSessions: () => request<Session[]>("/api/sessions"),
  getSession: (id: number) => request<Session>(`/api/sessions/${id}`),
  getLogs: (id: number) => request<LogEntry[]>(`/api/sessions/${id}/logs`),
  listRepos: () => request<Repo[]>("/api/repos"),
  getRepoMetadata: (repoId: number) => request<RepoMetadata>(`/api/repos/${repoId}/metadata`),
  getWiki: (repoId: number) => request<WikiResponse>(`/api/repos/${repoId}/wiki`),
  getWikiPage: (repoId: number, slug: string) =>
    request<WikiPage>(`/api/repos/${repoId}/wiki/pages/${encodeURIComponent(slug)}`),
  indexWiki: (repoId: number, force: boolean) =>
    request<WikiResponse>(`/api/repos/${repoId}/wiki/index`, {
      method: "POST",
      body: JSON.stringify({ force }),
    }),

  createSession: (body: { session_type: SessionType; repo: string | null; prompt: string | null }) =>
    request<Session>("/api/sessions", { method: "POST", body: JSON.stringify(body) }),

  sendMessage: (id: number, text: string) =>
    request<Session>(`/api/sessions/${id}/message`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  stopSession: (id: number) => request<Session>(`/api/sessions/${id}/stop`, { method: "POST" }),
  closeSession: (id: number) => request<Session>(`/api/sessions/${id}/close`, { method: "POST" }),
  archiveSession: (id: number) => request<Session>(`/api/sessions/${id}/archive`, { method: "POST" }),
  reopenSession: (id: number) => request<Session>(`/api/sessions/${id}/reopen`, { method: "POST" }),
};
