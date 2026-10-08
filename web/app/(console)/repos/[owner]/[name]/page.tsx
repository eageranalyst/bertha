"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

/** /repos/owner/name has no content of its own; send it to the metadata subtab. */
export default function RepoIndex() {
  const { owner, name } = useParams<{ owner: string; name: string }>();
  const router = useRouter();

  useEffect(() => {
    router.replace(`/repos/${owner}/${name}/metadata`);
  }, [owner, name, router]);

  return null;
}
