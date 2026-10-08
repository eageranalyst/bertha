"use client";

import { AgentContextPanel } from "@/components/AgentContextPanel";
import { useRepo } from "@/components/RepoContext";

export default function RepoAgentContextPage() {
  const repo = useRepo();
  if (!repo) return <div className="empty-state">Loading…</div>;
  return <AgentContextPanel repo={repo.full_name} />;
}
