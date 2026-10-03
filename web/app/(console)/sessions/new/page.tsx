"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ComposeDraft } from "@/components/ComposeDraft";
import { api } from "@/lib/api";
import type { Model, Repo, SessionType } from "@/lib/types";

export default function NewSessionPage() {
  const router = useRouter();
  const [repos, setRepos] = useState<Repo[]>([]);
  const [models, setModels] = useState<Model[]>([]);

  useEffect(() => {
    api.listRepos().then(setRepos).catch(() => {});
    api.listModels().then(setModels).catch(() => {});
  }, []);

  async function submit(
    sessionType: SessionType,
    repo: string | null,
    prompt: string | null,
    model: string | null,
  ) {
    try {
      const session = await api.createSession({ session_type: sessionType, repo, prompt, model });
      router.push(`/sessions/${session.ref}`);
    } catch (err) {
      alert(`Could not start session: ${(err as Error).message}`);
    }
  }

  return <ComposeDraft repos={repos} models={models} onSubmit={submit} />;
}
