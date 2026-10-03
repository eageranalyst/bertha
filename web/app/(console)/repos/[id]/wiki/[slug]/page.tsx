"use client";

import { useParams } from "next/navigation";
import { RepoWiki } from "@/components/RepoWiki";

export default function RepoWikiPage() {
  const { id, slug } = useParams<{ id: string; slug: string }>();
  return <RepoWiki repoId={Number(id)} slug={slug} />;
}
