"use client";

import { useRouter } from "next/navigation";
import { SkillForm } from "@/components/SkillForm";
import { api } from "@/lib/api";

export default function InstallSkillPage() {
  const router = useRouter();

  async function install(content: string) {
    const skill = await api.saveSkill(content);
    router.push(`/skills/${skill.name}`);
  }

  return (
    <div className="skill-page">
      <h2 className="wiki-title">Install a skill</h2>
      <p className="skill-hint">
        Paste or choose a SKILL.md. Its frontmatter needs a <code>name</code> and a{" "}
        <code>description</code>. Installing a skill whose name already exists updates it: the version
        goes up, and its metadata and repo links stay.
      </p>
      <SkillForm submitLabel="Install" onSubmit={install} />
    </div>
  );
}
