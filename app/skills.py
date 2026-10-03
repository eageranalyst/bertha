"""SKILL.md parsing for the skills library.

A skill is one markdown file in the format OpenCode loads: a frontmatter
block with `name` and `description`, then the body. The name becomes a
folder name inside the code-session container, so it's restricted to a
safe slug. OpenCode also skips skills without a description, so that's
required here too.
"""

import re

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def parse_skill(content: str) -> tuple[str, str]:
    """Returns (name, description) from a SKILL.md's frontmatter.

    Only single-line `key: value` pairs are read. Raises ValueError with a
    message fit to show the user when the file isn't a usable skill.
    """
    lines = content.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with a --- frontmatter block")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("the frontmatter block is not closed with ---")

    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("\"'")

    name = fields.get("name", "")
    description = fields.get("description", "")
    if not NAME_RE.match(name):
        raise ValueError(
            "name must be lowercase letters, digits and hyphens, up to 64 characters"
        )
    if not description:
        raise ValueError("description is required: say what the skill does and when to use it")
    return name, description
