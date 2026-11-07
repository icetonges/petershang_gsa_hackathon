"""Shared helpers: templating, naming, validation, and path safety.

The generators build source files by substituting `{{token}}` placeholders
rather than using f-strings or str.format, because generated Python contains
braces of its own (dicts, type parameters, f-strings) that would otherwise
need escaping.
"""

from __future__ import annotations

import keyword
import re
import textwrap
from pathlib import Path, PurePosixPath

TOKEN_RE = re.compile(r"\{\{([a-z0-9_]+)\}\}")
UC_PART_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")
SAFE_REL_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

PYTHON_RESERVED = set(keyword.kwlist) | {"match", "case", "type"}


class TemplateError(ValueError):
    """A template referenced a token that was not supplied."""


class UnsafePathError(ValueError):
    """A requested output path escaped the configured root."""


def render(template: str, values: dict[str, object]) -> str:
    """Substitute every `{{token}}` in `template` from `values`.

    Raises:
        TemplateError: if the template references a token with no value.
    """
    missing: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            missing.append(key)
            return match.group(0)
        return str(values[key])

    result = TOKEN_RE.sub(_replace, template)
    if missing:
        unique = ", ".join(sorted(set(missing)))
        raise TemplateError(f"template is missing values for: {unique}")
    return result


def slugify(text: str, max_length: int = 40) -> str:
    """Turn free text into a lowercase hyphenated slug."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    cleaned = cleaned.strip("-")[:max_length].strip("-")
    if not cleaned:
        return "mcp-server"
    if not cleaned[0].isalpha():
        cleaned = f"s-{cleaned}"[:max_length].strip("-")
    return cleaned


def to_identifier(text: str, max_length: int = 60) -> str:
    """Turn free text into a safe lowercase Python identifier."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    cleaned = cleaned[:max_length].strip("_")
    if not cleaned:
        return "field"
    if not cleaned[0].isalpha():
        cleaned = f"f_{cleaned}"
    if cleaned in PYTHON_RESERVED:
        cleaned = f"{cleaned}_"
    return cleaned


def validate_uc_identifier(part: str, label: str = "identifier") -> str:
    """Validate one Unity Catalog name part before it reaches SQL text.

    Catalog, schema, table, and column names are interpolated into generated
    and executed SQL, so they are checked against a strict pattern and then
    backtick-quoted. Never pass an unvalidated caller string into SQL.

    Raises:
        ValueError: if the part is not a plain identifier.
    """
    part = part.strip()
    if not UC_PART_RE.match(part):
        raise ValueError(
            f"{label} '{part}' is not a plain identifier; expected letters, "
            "digits, and underscores starting with a letter or underscore"
        )
    return part


def quote_uc_name(*parts: str) -> str:
    """Backtick-quote a validated dotted Unity Catalog name."""
    safe = [validate_uc_identifier(p) for p in parts if p]
    return ".".join(f"`{p}`" for p in safe)


def safe_output_path(root: str | Path, relative: str) -> Path:
    """Resolve `relative` inside `root`, refusing anything that escapes it.

    Raises:
        UnsafePathError: on absolute paths, traversal, or odd characters.
    """
    if not relative or relative != relative.strip():
        raise UnsafePathError("path must be a non-empty trimmed string")
    posix = relative.replace("\\", "/")
    if posix.startswith("/") or ":" in posix:
        raise UnsafePathError(f"path must be relative: {relative!r}")
    if not SAFE_REL_RE.match(posix):
        raise UnsafePathError(
            f"path {relative!r} may only contain letters, digits, dot, "
            "dash, underscore, and forward slash"
        )
    if any(segment in {"", ".", ".."} for segment in PurePosixPath(posix).parts):
        raise UnsafePathError(f"path {relative!r} may not contain . or ..")

    root_path = Path(root).expanduser().resolve()
    candidate = (root_path / posix).resolve()
    if candidate != root_path and root_path not in candidate.parents:
        raise UnsafePathError(f"path {relative!r} escapes the configured root")
    return candidate


def estimate_tokens(text: str) -> int:
    """Rough token estimate (about four characters per token)."""
    return max(1, len(text) // 4)


def bullet_list(items: list[str], indent: str = "- ") -> str:
    """Render a markdown bullet list, or a placeholder when empty."""
    if not items:
        return f"{indent}(none recorded)"
    return "\n".join(f"{indent}{item}" for item in items)


def python_literal(value: object) -> str:
    """Render a value as a Python literal for generated source."""
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        inner = ", ".join(python_literal(v) for v in value)
        return f"[{inner}]"
    if isinstance(value, dict):
        inner = ", ".join(
            f"{python_literal(k)}: {python_literal(v)}" for k, v in value.items()
        )
        return "{" + inner + "}"
    return python_literal(str(value))


def docstring_safe(text: str) -> str:
    """Make text safe to embed in a generated docstring.

    Both triple-quote forms are neutralized, because generated code is
    itself held in triple-quoted templates and either sentinel would
    terminate one early.
    """
    cleaned = text.replace("\\\\", "\\\\\\\\")
    cleaned = cleaned.replace('"""', '" " "')
    cleaned = cleaned.replace("'''", '" " "')
    return cleaned.strip()


def truncate(text: str, limit: int = 2000) -> str:
    """Shorten text for a tool response, flagging that it was cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} chars omitted]"


def wrap_text(text: str, width: int = 88, indent: str = "") -> str:
    """Wrap prose to a line limit, for embedding in generated documents."""
    body = textwrap.fill(
        " ".join(str(text).split()),
        width=max(20, width - len(indent)),
        break_long_words=False,
        break_on_hyphens=False,
    )
    return "\n".join(f"{indent}{line}" for line in body.splitlines())


def wrap_string_parts(text: str, indent: str = "", width: int = 88) -> str:
    """Render prose as wrapped, implicitly concatenated Python string parts.

    A formatter can rewrap code but never a long string literal, so prose
    destined for generated source is split here instead.
    """
    cleaned = str(text).replace("\\", "").replace('"', "'")
    chunks = textwrap.wrap(
        " ".join(cleaned.split()),
        width=max(20, width - len(indent) - 4),
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    lines = []
    for position, chunk in enumerate(chunks):
        suffix = "" if position == len(chunks) - 1 else " "
        lines.append(f'{indent}"{chunk}{suffix}"')
    return "\n".join(lines)


STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "for",
        "from",
        "in",
        "is",
        "it",
        "let",
        "me",
        "my",
        "of",
        "on",
        "or",
        "see",
        "so",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "want",
        "what",
        "which",
        "with",
    }
)


def derive_server_name(purpose: str, max_words: int = 4) -> str:
    """Build a short, readable server slug from a sentence of purpose.

    Slugifying a whole sentence produces a name nobody wants to type and,
    with the "mcp-" app prefix added, one the platform rejects outright.
    """
    words = [
        word
        for word in re.sub(r"[^a-z0-9\s]", " ", purpose.lower()).split()
        if word not in STOP_WORDS and len(word) > 2
    ]
    chosen = words[:max_words] or ["mcp", "server"]
    slug = slugify("-".join(chosen), max_length=34)
    if not slug.endswith("-mcp"):
        slug = f"{slug}-mcp"
    return slugify(slug, max_length=40)
