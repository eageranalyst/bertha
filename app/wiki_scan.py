"""Deterministic first pass over a checkout.

Nothing here calls a model. It walks the tree once and records what
kind of project it is -- languages, ecosystems and frameworks, infra,
docs, data-model files, tests -- as a Scan. The indexer uses that to
decide which wiki pages are worth writing and which files the model
gets to read for each one, so a repo with no database never gets a
data-model page, and one with no CI never gets a deployment page.
"""

import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

SKIP_DIRS = {
    "node_modules", "vendor", "dist", "build", "target", "coverage", "__pycache__",
    "venv", "env", "site-packages", "bower_components", "out", "tmp",
}
KEEP_DOT_DIRS = {".github", ".circleci"}
MAX_FILES = 20_000
MAX_LISTED = 200
MAX_MANIFEST_BYTES = 200_000

LANGUAGES = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".java": "Java", ".kt": "Kotlin",
    ".cs": "C#", ".php": "PHP", ".swift": "Swift", ".scala": "Scala",
    ".ex": "Elixir", ".exs": "Elixir", ".dart": "Dart",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".sql": "SQL", ".tf": "HCL", ".sh": "Shell", ".bash": "Shell",
    ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".vue": "Vue", ".svelte": "Svelte",
}

# Keys are lowercase because every basename is lowercased before lookup.
MANIFEST_ECOSYSTEMS = {
    "package.json": "Node.js", "pyproject.toml": "Python", "pipfile": "Python",
    "setup.py": "Python", "go.mod": "Go", "cargo.toml": "Rust", "gemfile": "Ruby",
    "pom.xml": "JVM", "build.gradle": "JVM", "build.gradle.kts": "JVM",
    "composer.json": "PHP", "mix.exs": "Elixir", "pubspec.yaml": "Dart",
    "package.swift": "Swift",
}

# Matched against the dependency keys of package.json.
NODE_FRAMEWORKS = {
    "next": "Next.js", "react": "React", "react-native": "React Native", "vue": "Vue",
    "svelte": "Svelte", "@angular/core": "Angular", "express": "Express",
    "fastify": "Fastify", "@nestjs/core": "NestJS", "prisma": "Prisma",
    "@prisma/client": "Prisma", "drizzle-orm": "Drizzle", "typeorm": "TypeORM",
    "sequelize": "Sequelize", "mongoose": "Mongoose", "tailwindcss": "Tailwind CSS",
    "vite": "Vite", "webpack": "webpack", "@trpc/server": "tRPC", "graphql": "GraphQL",
    "zod": "Zod", "jest": "Jest", "vitest": "Vitest", "@playwright/test": "Playwright",
    "cypress": "Cypress",
}

# Matched as whole words against the text of every other manifest.
TEXT_FRAMEWORKS = {
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask", "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic", "celery": "Celery", "alembic": "Alembic", "pytest": "pytest",
    "boto3": "boto3", "streamlit": "Streamlit", "langchain": "LangChain",
    "github.com/gin-gonic/gin": "Gin", "gorm.io/gorm": "GORM",
    "github.com/gofiber/fiber": "Fiber", "github.com/spf13/cobra": "Cobra",
    "axum": "Axum", "actix-web": "Actix Web", "tokio": "Tokio", "serde": "Serde",
    "rails": "Ruby on Rails", "sinatra": "Sinatra",
    "spring-boot": "Spring Boot", "org.springframework.boot": "Spring Boot", "quarkus": "Quarkus",
}

TOOLING_NAMES = {
    ".editorconfig", ".pre-commit-config.yaml", ".pre-commit-config.yml", "ruff.toml",
    ".ruff.toml", ".flake8", "mypy.ini", "tox.ini", "setup.cfg", "biome.json",
    "biome.jsonc", "tsconfig.json", "rustfmt.toml", "clippy.toml", ".golangci.yml",
    ".golangci.yaml", ".stylelintrc", "stylelint.config.js", "makefile", "justfile",
    "taskfile.yml", ".nvmrc", ".node-version", ".python-version", ".tool-versions",
}
TOOLING_PREFIXES = (".eslintrc", "eslint.config.", ".prettierrc", "prettier.config.")
TEST_NAMES = {"pytest.ini", "conftest.py", "karma.conf.js", ".mocharc.js", ".mocharc.json"}
TEST_CONFIG_PREFIXES = ("jest.config.", "vitest.config.", "playwright.config.")
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs"}
TEST_SUFFIXES = (
    "_test.py", "_test.go", "_spec.rb", ".test.ts", ".test.tsx", ".test.js",
    ".test.jsx", ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx",
)
ENTRYPOINT_NAMES = {
    "main.py", "app.py", "server.py", "manage.py", "__main__.py", "index.ts", "index.js",
    "index.tsx", "server.ts", "server.js", "main.ts", "main.js", "main.tsx", "app.ts",
    "app.js", "main.go", "main.rs", "main.java", "program.cs", "procfile",
}
DOC_EXCLUDED_ROOTS = ("changelog", "license", "code_of_conduct", "security", "notice")
DATA_NAMES = {
    "schema.sql", "structure.sql", "schema.rb", "openapi.yaml", "openapi.yml",
    "openapi.json", "swagger.yaml", "swagger.json",
}
DATA_EXTENSIONS = (".prisma", ".graphql", ".graphqls", ".proto", ".sql")
MODEL_EXTENSIONS = (".py", ".ts", ".go", ".rb", ".java", ".kt", ".cs", ".php", ".rs")

# (predicate over (relative path, lowercase basename), label)
INFRA_RULES: list[tuple[Any, str]] = [
    (lambda rel, name: name.startswith("dockerfile") or name.endswith(".dockerfile")
        or (name.startswith(("docker-compose", "compose.")) and name.endswith((".yml", ".yaml"))),
     "Docker"),
    (lambda rel, name: name.endswith(".tf"), "Terraform"),
    (lambda rel, name: name == "cdk.json", "AWS CDK"),
    (lambda rel, name: name in ("serverless.yml", "serverless.yaml"), "Serverless Framework"),
    (lambda rel, name: name == "chart.yaml", "Helm"),
    (lambda rel, name: rel.startswith(("k8s/", "kubernetes/", "deploy/", "infra/"))
        and name.endswith((".yml", ".yaml")), "Kubernetes / deploy manifests"),
    (lambda rel, name: name.startswith("pulumi.") or name == "pulumi.yaml", "Pulumi"),
    (lambda rel, name: name == "fly.toml", "Fly.io"),
    (lambda rel, name: name == "netlify.toml", "Netlify"),
    (lambda rel, name: name == "vercel.json", "Vercel"),
    (lambda rel, name: name == "procfile", "Procfile"),
]


@dataclass
class Scan:
    file_count: int = 0
    languages: list[dict[str, Any]] = field(default_factory=list)
    ecosystems: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    infra: list[str] = field(default_factory=list)
    top_dirs: list[dict[str, Any]] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    deploy_files: list[str] = field(default_factory=list)
    tooling: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    data_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    # Only used to build prompts; left out of to_dict().
    files: list[str] = field(default_factory=list, repr=False)
    tree: str = field(default="", repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        del data["files"]
        del data["tree"]
        return data


def scan_repo(root: Path) -> Scan:
    files, lang_bytes, top_dirs = _walk(root)
    scan = Scan(file_count=len(files), files=files)

    total = sum(lang_bytes.values())
    scan.languages = [
        {"name": name, "percent": round(100 * size / total, 1)}
        for name, size in lang_bytes.most_common(8)
        if total and 100 * size / total >= 0.5
    ]
    scan.top_dirs = [{"name": name, "files": n} for name, n in top_dirs.most_common(25)]

    manifests = _pick(files, lambda rel, name: _ecosystem(name) != ""
        or name.startswith("requirements") and name.endswith(".txt"))
    scan.manifests = manifests
    scan.ecosystems = sorted({_ecosystem(_basename(rel)) for rel in manifests} - {""})
    scan.frameworks = _frameworks(root, manifests)

    scan.workflows = _pick(files, lambda rel, name: rel.startswith(".github/workflows/")
        and name.endswith((".yml", ".yaml")))
    infra_labels: list[str] = []
    infra_files: list[str] = []
    for predicate, label in INFRA_RULES:
        hits = _pick(files, predicate)
        if hits:
            infra_labels.append(label)
            infra_files.extend(hits)
    if scan.workflows:
        infra_labels.append("GitHub Actions")
    scan.infra = infra_labels
    scan.deploy_files = list(dict.fromkeys([*infra_files, *scan.workflows]))[:MAX_LISTED]

    scan.tooling = _pick(files, lambda rel, name: name in TOOLING_NAMES
        or name.startswith(TOOLING_PREFIXES))
    scan.docs = _pick(files, _is_doc)
    scan.decisions = _pick(files, lambda rel, name: bool(
        re.search(r"(^|/)(adr|adrs|decisions|decision-records)/", rel)
        and name.endswith((".md", ".rst", ".txt", ".adoc"))))
    scan.data_files = _pick(files, _is_data)
    scan.test_files = _pick(files, _is_test)
    scan.entrypoints = _pick(files, lambda rel, name: rel.count("/") <= 2
        and name in ENTRYPOINT_NAMES and not _is_test(rel, name)
        or rel.startswith("cmd/") and name == "main.go")
    scan.tree = _render_tree(files)
    return scan


# Top-level definitions worth listing in a repo map, by file type. Only
# unindented lines, so nested functions and methods stay out.
SYMBOL_PATTERNS = {
    ".py": re.compile(r"^(?:async\s+def|def|class)\s+(\w+)", re.M),
    ".ts": re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|interface|type|enum)\s+(\w+)", re.M),
    ".go": re.compile(r"^(?:func\s+(?:\([^)]*\)\s+)?|type\s+)(\w+)", re.M),
    ".rb": re.compile(r"^(?:class|module|def)\s+([\w:.]+)", re.M),
    ".rs": re.compile(r"^pub\s+(?:fn|struct|enum|trait)\s+(\w+)", re.M),
    ".java": re.compile(r"^public\s+(?:final\s+|abstract\s+)?(?:class|interface|enum|record)\s+(\w+)", re.M),
}
for _ext in (".tsx", ".js", ".jsx", ".mjs"):
    SYMBOL_PATTERNS[_ext] = SYMBOL_PATTERNS[".ts"]
MAP_FILE_LIMIT = 400
MAP_MAX_CHARS = 10_000


def repo_map(root: Path, scan: Scan) -> str:
    """One line per source file with its top-level definitions, so the agent
    can see where things live without searching. Tests come last; the map is
    capped so it stays a small part of the prompt.
    """
    tests = set(scan.test_files)
    sources = [f for f in scan.files if PurePosixPath(f).suffix.lower() in SYMBOL_PATTERNS]
    sources.sort(key=lambda f: (f in tests, f.count("/"), f))
    lines: list[str] = []
    used = 0
    for rel in sources[:MAP_FILE_LIMIT]:
        pattern = SYMBOL_PATTERNS[PurePosixPath(rel).suffix.lower()]
        names = list(dict.fromkeys(pattern.findall(read_excerpt(root / rel, 60_000))))[:12]
        line = f"{rel}: {', '.join(names)}" if names else rel
        if used + len(line) > MAP_MAX_CHARS:
            lines.append(f"… and {len(sources) - len(lines)} more files")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def read_excerpt(path: Path, max_chars: int) -> str:
    """The first `max_chars` characters of a text file, or "" if it
    can't be read or looks binary.
    """
    try:
        with path.open("rb") as fh:
            raw = fh.read(max_chars * 4)
    except OSError:
        return ""
    if b"\x00" in raw[:1024]:
        return ""
    text = raw.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… (truncated)"
    return text


# -- walking ----------------------------------------------------------

def _walk(root: Path) -> tuple[list[str], Counter, Counter]:
    files: list[str] = []
    lang_bytes: Counter = Counter()
    top_dirs: Counter = Counter()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and (not d.startswith(".") or d in KEEP_DOT_DIRS)
        )
        for name in sorted(filenames):
            if len(files) >= MAX_FILES:
                return files, lang_bytes, top_dirs
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            files.append(rel)
            top_dirs["/".join(rel.split("/")[:-1][:1]) or "(root)"] += 1
            language = LANGUAGES.get(full.suffix.lower())
            if language:
                try:
                    lang_bytes[language] += full.stat().st_size
                except OSError:
                    pass
    return files, lang_bytes, top_dirs


def _pick(files: list[str], predicate: Any) -> list[str]:
    hits = []
    for rel in files:
        if predicate(rel, _basename(rel)):
            hits.append(rel)
            if len(hits) >= MAX_LISTED:
                break
    return hits


def _basename(rel: str) -> str:
    return PurePosixPath(rel).name.lower()


def _ecosystem(lowercase_name: str) -> str:
    """The ecosystem a manifest belongs to, or "" if it isn't one."""
    if lowercase_name.endswith(".csproj"):
        return ".NET"
    if lowercase_name.startswith("requirements") and lowercase_name.endswith(".txt"):
        return "Python"
    return MANIFEST_ECOSYSTEMS.get(lowercase_name, "")


def _is_doc(rel: str, name: str) -> bool:
    if name in ("claude.md", "agents.md", "contributing.md") or name.startswith("readme"):
        return True
    if rel.startswith("docs/") and name.endswith((".md", ".rst", ".adoc", ".txt")):
        return True
    if rel.startswith(".github/") and ("template" in name or name == "codeowners"):
        return True
    return (
        "/" not in rel
        and name.endswith((".md", ".rst", ".adoc"))
        and not name.startswith(DOC_EXCLUDED_ROOTS)
    )


def _is_data(rel: str, name: str) -> bool:
    if name in DATA_NAMES or name.endswith(DATA_EXTENSIONS):
        return True
    if "migrations/" in rel or "/versions/" in rel and "alembic" in rel:
        return name.endswith((".sql", ".py", ".rb", ".ts", ".js"))
    if name in ("models.py", "models.go") or ("/models/" in f"/{rel}" and name.endswith(MODEL_EXTENSIONS)):
        return True
    if name.endswith((".entity.ts", ".entity.java")) or "/entities/" in f"/{rel}":
        return True
    return name.startswith("schema.") and name.endswith((".ts", ".js", ".py", ".go")) and any(
        part in rel for part in ("db/", "database/", "drizzle")
    )


def _is_test(rel: str, name: str) -> bool:
    if any(part in TEST_DIRS for part in rel.split("/")[:-1]):
        return True
    if name in TEST_NAMES or name.startswith(TEST_CONFIG_PREFIXES):
        return True
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith(TEST_SUFFIXES)


def _frameworks(root: Path, manifests: list[str]) -> list[str]:
    found: set[str] = set()
    for rel in manifests:
        path = root / rel
        try:
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                continue
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if _basename(rel) == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            deps: dict[str, Any] = {}
            for section in ("dependencies", "devDependencies"):
                block = data.get(section)
                if isinstance(block, dict):
                    deps.update(block)
            for key in deps:
                if key in NODE_FRAMEWORKS:
                    found.add(NODE_FRAMEWORKS[key])
                elif key.startswith(("@aws-sdk/", "aws-cdk-lib")):
                    found.add("AWS SDK / CDK")
        else:
            lowered = text.lower()
            for needle, label in TEXT_FRAMEWORKS.items():
                if re.search(rf"(?<![\w./-]){re.escape(needle)}(?![\w-])", lowered):
                    found.add(label)
    return sorted(found)


def _render_tree(files: list[str], max_lines: int = 150) -> str:
    """Directories up to three levels deep with how many files sit under
    each, plus the root-level files. Enough for the model to see the shape.
    """
    dir_counts: Counter = Counter()
    for rel in files:
        parts = rel.split("/")[:-1]
        for depth in range(1, min(len(parts), 3) + 1):
            dir_counts["/".join(parts[:depth]) + "/"] += 1
    lines = [
        f"{'  ' * (d.count('/') - 1)}{d} ({n} files)"
        for d, n in sorted(dir_counts.items())[:max_lines]
    ]
    root_files = [f for f in files if "/" not in f][:40]
    if root_files:
        lines.append("root files: " + ", ".join(root_files))
    return "\n".join(lines)
