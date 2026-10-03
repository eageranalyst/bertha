"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

/** /repos/owner/name/wiki opens the overview page. */
export default function RepoWikiIndex() {
  const { owner, name } = useParams<{ owner: string; name: string }>();
  const router = useRouter();

  useEffect(() => {
    router.replace(`/repos/${owner}/${name}/wiki/overview`);
  }, [owner, name, router]);

  return null;
}
