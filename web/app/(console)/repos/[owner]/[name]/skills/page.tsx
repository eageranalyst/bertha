"use client";

import { RepoSkillsPanel } from "@/components/RepoSkillsPanel";
import { useRepo } from "@/components/RepoContext";

export default function RepoSkillsPage() {
  const repo = useRepo();
  if (!repo) return <div className="empty-state">Loading…</div>;
  return <RepoSkillsPanel repoId={repo.id} />;
}
