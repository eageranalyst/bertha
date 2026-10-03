"use client";

import { RepoMetadataPanel } from "@/components/RepoMetadataPanel";
import { useRepo } from "@/components/RepoContext";

export default function RepoMetadataPage() {
  const repo = useRepo();
  if (!repo) return <div className="empty-state">Loading…</div>;
  return <RepoMetadataPanel repoId={repo.id} />;
}
