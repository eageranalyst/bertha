const FRONTMATTER_RE = /^﻿?---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/;

/**
 * Splits a SKILL.md into its `---` frontmatter and the markdown body. Without
 * the split, the frontmatter renders as a rule and a run-on paragraph.
 */
export function splitFrontmatter(content: string): { frontmatter: string | null; body: string } {
  const match = content.match(FRONTMATTER_RE);
  if (!match) return { frontmatter: null, body: content };
  return { frontmatter: match[1], body: content.slice(match[0].length) };
}

/** Unix seconds to a local date-time string, or a dash when there isn't one. */
export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

/** A status as people read it: "pr_open" -> "PR open", "timed_out" -> "timed out". */
export function statusLabel(status: string): string {
  if (status === "pr_open") return "PR open";
  return status.replaceAll("_", " ");
}
