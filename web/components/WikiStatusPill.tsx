import type { WikiStatus } from "@/lib/types";

const LABELS: Record<WikiStatus | "none", string> = {
  ready: "wiki ready",
  indexing: "indexing",
  failed: "wiki failed",
  none: "no wiki",
};

export function WikiStatusPill({ status }: { status: WikiStatus | null }) {
  const key = status ?? "none";
  return <span className={`status status-wiki-${key}`}>{LABELS[key]}</span>;
}
