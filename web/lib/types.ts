export type SessionType = "ask" | "code";

export type SessionStatus =
  | "idle"
  | "queued"
  | "running"
  | "pr_open"
  | "sleeping"
  | "stopped"
  | "failed"
  | "closed"
  | "archived";

export interface Session {
  id: number;
  ref: string;
  session_type: SessionType;
  repo: string | null;
  issue_number: number | null;
  title: string;
  pr_number: number | null;
  branch_name: string | null;
  harness_session_id: string | null;
  status: SessionStatus;
  container_id: string | null;
  turn_count: number;
  last_seen_comment_id: number;
  last_activity_at: number;
  pending_message: string | null;
  /** What the session is waiting on from you, or null. */
  attention: AttentionReason | null;
  /** The registered model key the session runs on. */
  model: string | null;
  /** 1 when the user chose the model; 0 when routing picks it per turn. */
  model_pinned: number;
  /** 1 when the last turn failed, so the next one is routed up a tier. */
  last_turn_failed: number;
  /** 1 when the agent has Bertha's GitHub tools for the session's repo. */
  github_tools: number;
  /** The schedule that started this session, if any. */
  schedule_id: number | null;
  /** Where a spec-first session is; null for a normal session. */
  spec_phase: SpecPhase | null;
  /** The spec's path in the repo, once the first spec turn has run. */
  spec_path: string | null;
  created_at: number;
  updated_at: number;
}

export type ScheduleRunStatus = "running" | "succeeded" | "failed" | "stopped" | "timed_out";

export interface ScheduleRun {
  id: number;
  schedule_id: number;
  session_id: number | null;
  session_ref: string | null;
  session_status: SessionStatus | null;
  status: ScheduleRunStatus;
  reason: string | null;
  /** UTC epoch seconds. */
  scheduled_for: number;
  started_at: number;
  finished_at: number | null;
}

/** A prompt that runs on a cron schedule, each run as a normal session. */
export interface Schedule {
  id: number;
  name: string;
  repo: string | null;
  prompt: string;
  cron: string;
  /** IANA zone the cron is read in, e.g. Europe/London. */
  timezone: string;
  timeout_minutes: number;
  model: string | null;
  session_type: SessionType;
  github_tools: boolean;
  enabled: boolean;
  /** UTC epoch seconds, or null when disabled. */
  next_run_at: number | null;
  created_at: number;
  updated_at: number;
  last_run: ScheduleRun | null;
}

export type ScheduleInput = Pick<
  Schedule,
  "name" | "repo" | "prompt" | "cron" | "timezone" | "timeout_minutes" | "model" | "session_type" | "github_tools" | "enabled"
>;

/** One GitHub tool call made by a session's agent. */
export interface GitHubCall {
  id: number;
  tool: string;
  repo: string;
  issue_number: number | null;
  args: Record<string, unknown>;
  before: { title?: string; body?: string; labels?: string[]; state?: string } | null;
  after: { title?: string; body?: string; labels?: string[]; state?: string; comment_id?: number } | null;
  ok: boolean;
  error: string | null;
  undone_at: number | null;
  created_at: number;
}

/** How well agent containers are contained, from /api/security. */
export interface SecurityStatus {
  egress_isolation: boolean;
  egress_allow: string[];
  agent_user: string;
  container_limits: { memory_gb: number; cpus: number; pids: number };
  token: { kind: "classic" | "fine-grained"; scopes: string[]; broad_scopes: string[]; expires: string | null } | null;
  token_error: string | null;
  repos: { full_name: string; default_branch_protected: boolean | null }[];
}

/** One of the limits on a code turn, from the Settings screen. */
export interface AgentSetting {
  key: string;
  label: string;
  help: string;
  value: number;
  default: number;
  minimum: number;
  maximum: number;
}

/** A local model Bertha can run sessions on. */
export interface Model {
  key: string;
  model_id: string;
  tier: "light" | "standard" | "strong";
  description: string;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

/** replied: the agent answered and needs your reply. review: a PR is open. failed: the last turn errored. */
export type SpecPhase = "drafting" | "review" | "building" | "built" | "abandoned";

export interface SessionSpec {
  phase: SpecPhase;
  path: string | null;
  /** The spec as it is on the branch; null until it has been pushed. */
  content: string | null;
  url: string | null;
  pr_url: string | null;
}

export type AttentionReason ="replied" | "review" | "spec" | "failed";

export interface LogEntry {
  id: number;
  session_id: number;
  kind: string;
  content: string;
  created_at: number;
}

export type WikiStatus = "indexing" | "ready" | "failed";

export interface Repo {
  id: number;
  full_name: string;
  created_at: number;
  wiki_status: WikiStatus | null;
  wiki_last_indexed_at: number | null;
}

/** What the deterministic scan found in the checkout. */
export interface WikiScan {
  file_count: number;
  languages: { name: string; percent: number }[];
  ecosystems: string[];
  frameworks: string[];
  infra: string[];
  manifests: string[];
  workflows: string[];
  docs: string[];
  decisions: string[];
  data_files: string[];
  test_files: string[];
  entrypoints: string[];
}

export interface WikiIndex {
  repo: string;
  status: WikiStatus;
  progress: string | null;
  commit_sha: string | null;
  error: string | null;
  scan: WikiScan | null;
  /** When the wiki was first created. */
  created_at: number;
  /** When the most recent build finished. */
  last_indexed_at: number | null;
  /** When the index row last changed for any reason. */
  updated_at: number;
}

export interface WikiPageSummary {
  slug: string;
  title: string;
  section: string;
  position: number;
  created_at: number;
  updated_at: number;
}

export interface WikiPage extends WikiPageSummary {
  body: string;
}

export interface WikiResponse {
  index: WikiIndex | null;
  pages: WikiPageSummary[];
}

export interface GitHubRepoInfo {
  description: string | null;
  default_branch: string;
  language: string | null;
  private: boolean;
  archived: boolean;
  html_url: string;
  pushed_at: number | null;
  open_issues: number;
}

export interface Skill {
  id: number;
  name: string;
  description: string;
  version: number;
  created_at: number;
  updated_at: number;
  metadata: Record<string, string>;
}

/** A skill with its markdown body, from the single-skill endpoints. */
export interface SkillWithContent extends Skill {
  content: string;
}

/** A library skill as one repo sees it. */
export interface RepoSkill extends Skill {
  enabled: boolean;
}

export interface RepoMetadata {
  repo: Repo;
  github: GitHubRepoInfo | null;
  github_error: string | null;
  sessions: { total: number; active: number; with_pr: number };
  wiki: WikiIndex | null;
}
