"""Loader for the bundled skill and knowledge library.

The skills ship *inside* the package rather than in `.agents/` so that a
deployed server can serve them. `.dockerignore` excludes `.agents/`, and a
Databricks App only receives the synced source tree, so anything the running
server must read has to live under `src/mcp_server_creator/`.

Skills follow the Agent Skills format: a directory containing `SKILL.md` with
YAML frontmatter (`name`, `description`) plus optional `references/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = PACKAGE_DIR / "skills"
KNOWLEDGE_DIR = PACKAGE_DIR / "knowledge"


class LibraryError(LookupError):
    """A requested skill or knowledge document does not exist."""


@dataclass
class Skill:
    """One bundled skill: its metadata, body, and reference files."""

    name: str
    description: str
    path: Path
    body: str
    references: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.body) // 4)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split simple `key: value` YAML frontmatter from a markdown body."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    if closing is None:
        return {}, text

    meta: dict[str, str] = {}
    current_key: str | None = None
    for line in lines[1:closing]:
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) and current_key:
            meta[current_key] = f"{meta[current_key]} {line.strip()}".strip()
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current_key = key.strip()
            meta[current_key] = value.strip().strip("\"'")
    body = "\n".join(lines[closing + 1 :]).lstrip("\n")
    return meta, body


@lru_cache(maxsize=1)
def _skill_index() -> dict[str, Skill]:
    """Discover every skill directory under `skills/`."""
    index: dict[str, Skill] = {}
    if not SKILLS_DIR.is_dir():
        return index
    for directory in sorted(SKILLS_DIR.iterdir()):
        skill_file = directory / "SKILL.md"
        if not (directory.is_dir() and skill_file.is_file()):
            continue
        raw = skill_file.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        references = []
        reference_dir = directory / "references"
        if reference_dir.is_dir():
            references = sorted(
                f"references/{item.name}"
                for item in reference_dir.iterdir()
                if item.is_file()
            )
        name = meta.get("name") or directory.name
        index[name] = Skill(
            name=name,
            description=meta.get("description", ""),
            path=directory,
            body=body,
            references=references,
            metadata={
                k: v for k, v in meta.items() if k not in {"name", "description"}
            },
        )
    return index


def list_skills() -> list[dict[str, object]]:
    """Metadata for every bundled skill, cheap enough to list on every call."""
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "references": skill.references,
            "approx_tokens": skill.token_estimate,
        }
        for skill in _skill_index().values()
    ]


def skill_names() -> list[str]:
    return sorted(_skill_index())


def load_skill(name: str) -> Skill:
    """Load one skill by name.

    Raises:
        LibraryError: if no skill has that name.
    """
    index = _skill_index()
    key = name.strip()
    if key in index:
        return index[key]
    lowered = {k.lower(): k for k in index}
    if key.lower() in lowered:
        return index[lowered[key.lower()]]
    available = ", ".join(sorted(index)) or "(none installed)"
    raise LibraryError(f"no skill named '{name}'. Available: {available}")


def load_skill_reference(name: str, relative: str) -> str:
    """Read a reference file belonging to a skill.

    Raises:
        LibraryError: if the skill or the reference file does not exist.
    """
    skill = load_skill(name)
    if relative not in skill.references:
        available = ", ".join(skill.references) or "(no reference files)"
        raise LibraryError(
            f"'{relative}' is not a reference of skill '{skill.name}'. "
            f"Available: {available}"
        )
    target = (skill.path / relative).resolve()
    if skill.path.resolve() not in target.parents:
        raise LibraryError("reference path escaped the skill directory")
    return target.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _knowledge_index() -> dict[str, Path]:
    """Discover knowledge documents by slug."""
    if not KNOWLEDGE_DIR.is_dir():
        return {}
    return {
        item.stem.replace("_", "-"): item for item in sorted(KNOWLEDGE_DIR.glob("*.md"))
    }


def list_knowledge() -> list[dict[str, object]]:
    """Slug, title, and size for every knowledge document."""
    entries: list[dict[str, object]] = []
    for slug, path in _knowledge_index().items():
        first_line = ""
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("# "):
                    first_line = line[2:].strip()
                    break
        entries.append(
            {
                "slug": slug,
                "title": first_line or slug,
                "approx_tokens": max(1, path.stat().st_size // 4),
            }
        )
    return entries


def load_knowledge(slug: str) -> str:
    """Read one knowledge document.

    Raises:
        LibraryError: if no document has that slug.
    """
    index = _knowledge_index()
    key = slug.strip().replace("_", "-")
    if key not in index:
        available = ", ".join(sorted(index)) or "(none installed)"
        raise LibraryError(f"no knowledge document '{slug}'. Available: {available}")
    return index[key].read_text(encoding="utf-8")
