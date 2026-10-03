/**
 * Checks and fixes for a SKILL.md, against the same rules the API enforces
 * (app/skills.py) plus the description guidance OpenCode relies on.
 *
 * Fixes only touch what can be fixed mechanically: line endings, the BOM,
 * a missing frontmatter block, and an invalid name. A missing description is
 * an error the author has to write, since it can't be invented here.
 */

export type SkillIssue = { level: "error" | "warning"; message: string };

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const FRONTMATTER_RE = /^---\n([\s\S]*?)\n---[ \t]*(?:\n|$)/;

/** Line endings as LF and no BOM, so the rules below see one shape. */
function normalize(content: string): string {
  return content.replace(/^﻿/, "").replace(/\r\n?/g, "\n");
}

/** Frontmatter as key/value pairs, or null when there's no frontmatter block. */
export function readFrontmatter(content: string): Record<string, string> | null {
  const match = normalize(content).match(FRONTMATTER_RE);
  if (!match) return null;
  const fields: Record<string, string> = {};
  for (const line of match[1].split("\n")) {
    if (line.includes(":") && !/^\s/.test(line)) {
      const [key, ...rest] = line.split(":");
      fields[key.trim()] = rest.join(":").trim().replace(/^["']|["']$/g, "");
    }
  }
  return fields;
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/, "");
}

/** Problems with the content. `expectedName` is the skill's current name when editing. */
export function checkSkill(content: string, expectedName?: string): SkillIssue[] {
  const issues: SkillIssue[] = [];
  const text = normalize(content);
  const fields = readFrontmatter(text);

  if (!text.trim()) {
    return [{ level: "error", message: "The file is empty." }];
  }
  if (fields === null) {
    issues.push({
      level: "error",
      message: "Missing frontmatter. The file must start with a --- block holding name and description.",
    });
    return issues;
  }

  const name = fields.name ?? "";
  if (!name) {
    issues.push({ level: "error", message: "The frontmatter has no name." });
  } else if (name === "new") {
    issues.push({ level: "error", message: "'new' is reserved; choose another name." });
  } else if (!NAME_RE.test(name)) {
    issues.push({
      level: "error",
      message: "The name must be lowercase letters, digits and hyphens, up to 64 characters.",
    });
  } else if (expectedName && name !== expectedName) {
    issues.push({
      level: "error",
      message: `The name is ${expectedName}. Changing it would make a different skill; install this as a new skill instead.`,
    });
  }

  const description = fields.description ?? "";
  if (!description) {
    issues.push({
      level: "error",
      message: "The description is required. Say what the skill does and when to use it.",
    });
  } else {
    if (description.length < 40) {
      issues.push({
        level: "warning",
        message: "The description is short. Say what the skill does in more detail.",
      });
    }
    if (!/\buse (only )?when\b/i.test(description)) {
      issues.push({
        level: "warning",
        message: "Descriptions work best when they say when to use the skill, for example “Use when …”.",
      });
    }
  }

  const body = text.slice(text.match(FRONTMATTER_RE)?.[0].length ?? 0).trim();
  if (!body) {
    issues.push({ level: "warning", message: "The body is empty; add the instructions the model should follow." });
  }
  return issues;
}

/** Applies the mechanical fixes. Returns the content unchanged when there's nothing to fix. */
export function fixSkill(content: string): string {
  const text = normalize(content);
  const match = text.match(FRONTMATTER_RE);

  if (!match) {
    const h1 = text.match(/^#\s+(.+)$/m)?.[1] ?? "";
    const name = slugify(h1) || "new-skill";
    const body = text.replace(/^\s+/, "");
    return `---\nname: ${name === "new" ? "new-skill" : name}\ndescription: \n---\n\n${body}`;
  }

  const lines = match[1].split("\n");
  const fields = readFrontmatter(text) ?? {};
  const fixedName = slugify(fields.name ?? "") || slugify(text.match(/^#\s+(.+)$/m)?.[1] ?? "");
  const nameValue = fixedName && fixedName !== "new" ? fixedName : "new-skill";

  let sawName = false;
  let sawDescription = false;
  const rewritten = lines.map((line) => {
    if (/^name:/.test(line)) {
      sawName = true;
      return `name: ${nameValue}`;
    }
    if (/^description:/.test(line)) {
      sawDescription = true;
      return `description: ${(fields.description ?? "").replace(/\n/g, " ")}`;
    }
    return line.trimEnd();
  });
  if (!sawName) rewritten.unshift(`name: ${nameValue}`);
  if (!sawDescription) rewritten.push("description: ");

  const body = text.slice(match[0].length);
  return `---\n${rewritten.join("\n")}\n---\n${body}`;
}
