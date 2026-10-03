"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

/** /repos/:id has no content of its own; send it to the metadata subtab. */
export default function RepoIndex() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  useEffect(() => {
    router.replace(`/repos/${id}/metadata`);
  }, [id, router]);

  return null;
}
