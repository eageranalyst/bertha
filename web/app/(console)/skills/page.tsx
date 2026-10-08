import Link from "next/link";

export default function SkillsIndex() {
  return (
    <div className="empty-state">
      Select a skill, or <Link href="/skills/new">install one</Link>. Skills enabled on a repo are
      available to its code sessions.
    </div>
  );
}
