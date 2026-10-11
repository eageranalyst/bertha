const FRONTMATTER_RE = /^﻿?---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/;

/** A model in a picker: its name and tier, and where it runs when that's not this machine. */
export function modelLabel(m: { key: string; tier: string; provider?: string }): string {
  const hosted = m.provider && m.provider !== "local" ? `, ${m.provider}` : "";
  return `${m.key} (${m.tier}${hosted})`;
}

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

/** Unix seconds as "3 hours ago", "2 days ago"; a dash when there isn't one. */
export function fmtAgo(ts: number | null | undefined): string {
  if (!ts) return "—";
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  const units: [number, string][] = [[31536000, "year"], [2592000, "month"], [86400, "day"], [3600, "hour"], [60, "minute"]];
  for (const [size, name] of units) {
    const n = Math.floor(seconds / size);
    if (n >= 1) return `${n} ${name}${n === 1 ? "" : "s"} ago`;
  }
  return "just now";
}
