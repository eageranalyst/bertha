"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { RepoList } from "@/components/RepoList";
import { api } from "@/lib/api";
import type { Repo } from "@/lib/types";

/** The repo list, kept mounted while the detail pane changes route. */
export default function ReposLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [repos, setRepos] = useState<Repo[]>([]);

  const refresh = useCallback(async () => {
    try {
      setRepos(await api.listRepos());
    } catch {
      // API unreachable: keep the last list and retry on the next tick.
    }
  }, []);

  // Polled every few seconds because a running wiki build changes the status.
  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [refresh]);

  // /repos/owner/name/... -- the first two segments are the repo.
  const match = pathname.match(/^\/repos\/([^/]+)\/([^/]+)/);
  const selectedName = match ? `${match[1]}/${match[2]}` : null;

  return (
    <main>
      <RepoList repos={repos} selectedName={selectedName} />
      <section id="detail-pane">{children}</section>
    </main>
  );
}
