"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { SkillList } from "@/components/SkillList";
import { api } from "@/lib/api";
import type { Skill } from "@/lib/types";

/** The skills library list, kept mounted while the detail pane changes route. */
export default function SkillsLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [skills, setSkills] = useState<Skill[]>([]);

  const refresh = useCallback(async () => {
    try {
      setSkills(await api.listSkills());
    } catch {
      // API unreachable: keep the last list and retry on the next tick.
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  const match = pathname.match(/^\/skills\/([^/]+)$/);
  const selectedName = match && match[1] !== "new" ? decodeURIComponent(match[1]) : null;

  return (
    <main>
      <SkillList skills={skills} selectedName={selectedName} />
      <section id="detail-pane">{children}</section>
    </main>
  );
}
