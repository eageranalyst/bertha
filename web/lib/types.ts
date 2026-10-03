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

export interface Repo {
  id: number;
  full_name: string;
  created_at: number;
}
