import type {
  AgentSetting,
  LogEntry,
  Model,
  RepoMetadata,
  RepoSkill,
  Repo,
  Session,
  SessionType,
  Skill,
  SkillWithContent,
  WikiPage,
  WikiResponse,
} from "./types";

const API_ORIGIN = process.env.NEXT_PUBLIC_API_ORIGIN ?? "http://127.0.0.1:8787";

/** A failed API call. `message` is human text only; the raw body stays out of it. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The server's `detail` text when it sent one, otherwise a plain fallback. */
async function errorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => (d as { msg?: string }).msg ?? "invalid value").join("; ");
    }
  } catch {
    // Not JSON (an HTML error page, say); fall through to the generic text.
  }
  return res.status === 404 ? "Not found" : `Request failed (${res.status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_ORIGIN}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(0, `Can't reach the API at ${API_ORIGIN}`);
  }
  if (!res.ok) {
    throw new ApiError(res.status, await errorMessage(res));
  }
  return res.json() as Promise<T>;
}

export const api = {
  listSessions: () => request<Session[]>("/api/sessions"),
  getSession: (id: number) => request<Session>(`/api/sessions/${id}`),
  getSessionByRef: (ref: string) =>
    request<Session>(`/api/sessions/by-ref/${encodeURIComponent(ref)}`),
  getLogs: (id: number) => request<LogEntry[]>(`/api/sessions/${id}/logs`),
  listRepos: () => request<Repo[]>("/api/repos"),
  getRepoMetadata: (repoId: number) => request<RepoMetadata>(`/api/repos/${repoId}/metadata`),
  getWiki: (repoId: number) => request<WikiResponse>(`/api/repos/${repoId}/wiki`),
  getWikiPage: (repoId: number, slug: string) =>
    request<WikiPage>(`/api/repos/${repoId}/wiki/pages/${encodeURIComponent(slug)}`),
  listSkills: () => request<Skill[]>("/api/skills"),
  getSkillByName: (name: string) =>
    request<SkillWithContent>(`/api/skills/by-name/${encodeURIComponent(name)}`),
  saveSkill: (content: string) =>
    request<SkillWithContent & { created: boolean }>("/api/skills", {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  setSkillMetadata: (skillId: number, metadata: Record<string, string>) =>
    request<SkillWithContent>(`/api/skills/${skillId}/metadata`, {
      method: "PUT",
      body: JSON.stringify({ metadata }),
    }),
  deleteSkill: (skillId: number) =>
    request<{ ok: boolean }>(`/api/skills/${skillId}`, { method: "DELETE" }),
  listRepoSkills: (repoId: number) => request<RepoSkill[]>(`/api/repos/${repoId}/skills`),
  setRepoSkill: (repoId: number, skillId: number, enabled: boolean) =>
    request<RepoSkill[]>(`/api/repos/${repoId}/skills/${skillId}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  indexWiki: (repoId: number, force: boolean) =>
    request<WikiResponse>(`/api/repos/${repoId}/wiki/index`, {
      method: "POST",
      body: JSON.stringify({ force }),
    }),

  listSettings: () => request<AgentSetting[]>("/api/settings"),
  updateSettings: (values: Record<string, number>) =>
    request<AgentSetting[]>("/api/settings", { method: "PUT", body: JSON.stringify(values) }),
  listModels: () => request<Model[]>("/api/models"),
  refreshModels: () => request<Model[]>("/api/models/refresh", { method: "POST" }),
  updateModel: (key: string, patch: Partial<Pick<Model, "tier" | "description" | "enabled">>) =>
    request<Model>(`/api/models/${encodeURIComponent(key)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  createSession: (body: {
    session_type: SessionType;
    repo: string | null;
    prompt: string | null;
    model: string | null;
  }) =>
    request<Session>("/api/sessions", { method: "POST", body: JSON.stringify(body) }),

  sendMessage: (id: number, text: string) =>
    request<Session>(`/api/sessions/${id}/message`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  /** Pins the session to a model, or hands it back to routing when `model` is null. */
  setSessionModel: (id: number, model: string | null) =>
    request<Session>(`/api/sessions/${id}/model`, {
      method: "PUT",
      body: JSON.stringify({ model }),
    }),
  stopSession: (id: number) => request<Session>(`/api/sessions/${id}/stop`, { method: "POST" }),
  closeSession: (id: number) => request<Session>(`/api/sessions/${id}/close`, { method: "POST" }),
  archiveSession: (id: number) => request<Session>(`/api/sessions/${id}/archive`, { method: "POST" }),
  reopenSession: (id: number) => request<Session>(`/api/sessions/${id}/reopen`, { method: "POST" }),
};
