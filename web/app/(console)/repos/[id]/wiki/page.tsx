"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

/** /repos/:id/wiki opens the overview page. */
export default function RepoWikiIndex() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  useEffect(() => {
    router.replace(`/repos/${id}/wiki/overview`);
  }, [id, router]);

  return null;
}
