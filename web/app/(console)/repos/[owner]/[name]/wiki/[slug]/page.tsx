"use client";

import { useParams } from "next/navigation";
import { useRepo } from "@/components/RepoContext";
import { RepoWiki } from "@/components/RepoWiki";

export default function RepoWikiPage() {
  const repo = useRepo();
  const { slug } = useParams<{ slug: string }>();
  if (!repo) return <div className="empty-state">Loading…</div>;
  return <RepoWiki repoId={repo.id} repoName={repo.full_name} slug={slug} />;
}
