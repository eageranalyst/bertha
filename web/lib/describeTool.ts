// Turns an agent's tool call into a plain-English line for the transcript:
// what it did ("Searched file names for"), on what (`web/**/*ooter*`), and
// what came of it ("2 files"). Unknown tools fall back to their name.

import type { ToolEvent } from "./parseAgentLog";

export type ToolKind = "search" | "read" | "edit" | "run" | "github" | "wiki" | "helper" | "other";

export interface ToolDescription {
  kind: ToolKind;
  /** What it did, as a sentence start: "Read", "Searched code for". */
  verb: string;
  /** What it did it to, shown as code: a path, pattern, command or issue. */
  target?: string;
  /** Plain text after the target: "in web/", "lines 10–40". */
  where?: string;
  /** How it went: "12 matches", "+4 −1", "no files". */
  result?: string;
  /** For grouping a run of calls, "Read 10 issues": the noun ("issues"),
   *  and the verb when the verb above doesn't fit ("Ran 3 code searches"). */
  noun: string;
  groupVerb?: string;
}

/** A run of calls as one line: "Read 10 issues", "Ran 4 commands". */
export function groupLabel(d: ToolDescription, n: number): string {
  return `${d.groupVerb ?? d.verb.split(" ")[0]} ${n} ${d.noun}`;
}

const REPO = "/data/repo/";
// Where OpenCode saves a tool result too long to show the agent in full.
const SAVED_RESULT = /\/data\/\.local\/share\/opencode\/tool-output\/\S+/g;

function str(value: unknown): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  return String(value);
}

/** A path as the repo knows it: /data/repo/web/app.tsx -> web/app.tsx. */
export function repoPath(path: unknown): string | undefined {
  const p = str(path);
  if (!p) return undefined;
  if (p === "/data/repo" || p === REPO) return ".";
  return p.startsWith(REPO) ? p.slice(REPO.length) : p;
}

function within(path: unknown): string | undefined {
  const p = repoPath(path);
  return p && p !== "." ? `in ${p}` : undefined;
}

function count(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

function outputLines(output?: string): string[] {
  return (output ?? "").split("\n").filter((l) => l.trim());
}

function firstLine(text: string, max = 90): string {
  const line = text.split("\n")[0].trim().replaceAll(REPO, "").replace(SAVED_RESULT, "‹saved result›");
  return line.length > max ? line.slice(0, max - 1) + "…" : line;
}

function diffStats(diff?: string): string | undefined {
  if (!diff) return undefined;
  let added = 0;
  let removed = 0;
  for (const line of diff.split("\n")) {
    if (line.startsWith("+") && !line.startsWith("+++")) added++;
    else if (line.startsWith("-") && !line.startsWith("---")) removed++;
  }
  return added || removed ? `+${added} −${removed}` : undefined;
}

const GITHUB: Record<string, (i: Record<string, unknown>) => Omit<ToolDescription, "kind">> = {
  list_issues: (i) => ({ verb: "Listed", where: `${str(i.state) ?? "open"} issues`, noun: "issue lists", groupVerb: "Fetched" }),
  get_issue: (i) => ({ verb: "Read issue", target: `#${i.number}`, noun: "issues" }),
  list_labels: () => ({ verb: "Listed the repo's labels", noun: "label lists" }),
  pr_status: (i) => ({ verb: "Checked", target: i.number ? `PR #${i.number}` : "the PR", where: "and its CI", noun: "PR checks", groupVerb: "Ran" }),
  check_logs: (i) => ({ verb: "Read the CI log", where: i.job_id ? `for job ${i.job_id}` : undefined, noun: "CI logs" }),
  github_get: (i) => ({ verb: "Fetched from GitHub", target: str(i.path), noun: "GitHub requests", groupVerb: "Made" }),
  update_issue: (i) => ({ verb: "Updated issue", target: `#${i.number}`, noun: "issues" }),
  comment_issue: (i) => ({ verb: "Commented on issue", target: `#${i.number}`, noun: "comments", groupVerb: "Left" }),
  close_issue: (i) => ({ verb: "Closed issue", target: `#${i.number}`, noun: "issues" }),
  push_branch: () => ({ verb: "Pushed the branch", noun: "pushes" }),
  create_pr: () => ({ verb: "Opened a pull request", noun: "pull requests" }),
  update_pr: () => ({ verb: "Updated the pull request", noun: "PR updates" }),
  comment_pr: () => ({ verb: "Commented on the pull request", noun: "comments", groupVerb: "Left" }),
};

const WIKI: Record<string, (i: Record<string, unknown>) => Omit<ToolDescription, "kind">> = {
  wiki_pages: () => ({ verb: "Listed the wiki's pages", noun: "page lists" }),
  wiki_page: (i) => ({ verb: "Read wiki page", target: str(i.slug), noun: "wiki pages" }),
  wiki_search: (i) => ({ verb: "Searched the wiki for", target: str(i.query), noun: "wiki searches", groupVerb: "Ran" }),
  repo_map: (i) => ({ verb: "Read the repo map", where: within(i.path), noun: "repo maps" }),
  search_code: (i) => ({ verb: "Looked up code about", target: str(i.query), noun: "code lookups", groupVerb: "Made" }),
  context_notes: () => ({ verb: "Listed saved notes", noun: "note lists" }),
  context_note: (i) => ({ verb: "Read saved note", target: `#${i.id}`, noun: "notes" }),
};

export function describeTool(event: ToolEvent): ToolDescription {
  const tool = event.tool ?? "";
  const i = event.input ?? {};
  const out = event.output;

  if (tool.startsWith("github_")) {
    const d = GITHUB[tool.slice("github_".length)]?.(i);
    if (d) return { kind: "github", ...d };
  }
  if (tool.startsWith("bertha_")) {
    const d = WIKI[tool.slice("bertha_".length)]?.(i);
    if (d) return { kind: "wiki", ...d };
  }

  switch (tool) {
    case "glob": {
      const files = /^No files found/.test(out ?? "") ? 0 : outputLines(out).length;
      return { kind: "search", verb: "Searched file names for", target: str(i.pattern), where: within(i.path),
               result: event.status === "completed" ? (files ? count(files, "file") : "no files") : undefined,
               noun: "file-name searches", groupVerb: "Ran" };
    }
    case "grep": {
      const found = /Found (\d+) match/.exec(out ?? "");
      return { kind: "search", verb: "Searched code for", target: str(i.pattern),
               where: [within(i.path), str(i.include) && `in ${i.include} files`].filter(Boolean).join(" ") || undefined,
               result: found ? count(Number(found[1]), "match", "matches")
                 : event.status === "completed" ? "no matches" : undefined,
               noun: "code searches", groupVerb: "Ran" };
    }
    case "read": {
      if (typeof i.filePath === "string" && i.filePath.match(SAVED_RESULT)) {
        return { kind: "read", verb: "Read the rest of a long earlier result", noun: "saved results" };
      }
      const from = Number(i.offset) || 0;
      const limit = Number(i.limit) || 0;
      return { kind: "read", verb: "Read", target: repoPath(i.filePath),
               where: limit ? `lines ${from + 1}–${from + limit}` : undefined, noun: "files" };
    }
    case "list":
      return { kind: "read", verb: "Listed the folder", target: repoPath(i.path) ?? ".", noun: "folders" };
    case "edit":
    case "multiedit":
      return { kind: "edit", verb: "Edited", target: repoPath(i.filePath) ?? event.title, result: diffStats(event.diff),
               noun: "edits", groupVerb: "Made" };
    case "write": {
      const lines = typeof i.content === "string" ? i.content.split("\n").length : 0;
      return { kind: "edit", verb: "Wrote", target: repoPath(i.filePath) ?? event.title,
               result: lines ? count(lines, "line") : undefined, noun: "files" };
    }
    case "patch":
      return { kind: "edit", verb: "Applied a patch", result: diffStats(event.diff), noun: "patches", groupVerb: "Applied" };
    case "bash": {
      const lines = /^\(no output\)$/.test((out ?? "").trim()) ? 0 : outputLines(out).length;
      return { kind: "run", verb: "Ran", target: firstLine(str(i.command) ?? event.title ?? ""),
               result: event.status === "completed" ? (lines ? count(lines, "line") + " of output" : "no output") : undefined,
               noun: "commands" };
    }
    case "webfetch":
      return { kind: "read", verb: "Fetched", target: str(i.url), noun: "pages" };
    case "task":
      return { kind: "helper", verb: "Handed to a helper:", target: undefined, where: str(i.description) ?? event.title,
               noun: "helper tasks", groupVerb: "Started" };
    case "skill":
      return { kind: "read", verb: "Loaded the skill", target: str(i.name), noun: "skills" };
  }
  const name = tool.replace(/^[a-z]+_/, "").replace(/_/g, " ");
  return { kind: "other", verb: name.charAt(0).toUpperCase() + name.slice(1), target: event.title || undefined,
           noun: `${name} calls`, groupVerb: "Made" };
}

/** Why a call failed, in a few words, when it did. */
export function failureReason(event: ToolEvent): string | undefined {
  if (event.status !== "error") return undefined;
  const error = event.error ?? "";
  if (/rejected permission/i.test(error)) return "blocked by Bertha's permissions";
  if (/File not found|No such file/i.test(error)) return "not found";
  return "failed";
}
