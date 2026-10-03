"use client";

import { useParams } from "next/navigation";
import { RepoMetadataPanel } from "@/components/RepoMetadataPanel";

export default function RepoMetadataPage() {
  const { id } = useParams<{ id: string }>();
  return <RepoMetadataPanel repoId={Number(id)} />;
}
