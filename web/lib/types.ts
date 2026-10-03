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
  created_at: number;
  updated_at: number;
}

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

export interface RepoMetadata {
  repo: Repo;
  github: GitHubRepoInfo | null;
  github_error: string | null;
  sessions: { total: number; active: number; with_pr: number };
  wiki: WikiIndex | null;
}
