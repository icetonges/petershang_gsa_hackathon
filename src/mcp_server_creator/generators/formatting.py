"""Format generated Python before it leaves the generator.

A generated project's CI runs `ruff check` and `ruff format --check`. Emitting
code that fails its own lint step on the first commit is a bad first
impression and a real cost to the user, so the generator formats its output
rather than hoping the templates happen to match the formatter.

Ruff is invoked with `--isolated` and an explicit line length so the result
does not depend on whatever configuration happens to sit above the working
directory. If ruff is unavailable the source is returned unchanged: a
slightly unformatted project still runs, and `next_steps` tells the user to
run the formatter once.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache

LINE_LENGTH = 88
TIMEOUT_SECONDS = 30


@lru_cache(maxsize=1)
def ruff_available() -> bool:
    """True when a ruff executable can be found on the path."""
    return shutil.which("ruff") is not None


def _run(args: list[str], source: str) -> str | None:
    """Pipe source through ruff, returning None on any failure."""
    try:
        completed = subprocess.run(
            args,
            input=source,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout:
        return None
    return completed.stdout


def format_python(
    source: str, filename: str = "generated.py", first_party: str = ""
) -> str:
    """Sort imports and format one Python source string.

    `first_party` is the generated package name. Without it an isolated ruff
    treats the project's own imports as third-party and sorts them into the
    wrong block, which the project's own lint step then flags.

    Returns the source unchanged when ruff is unavailable or either pass
    fails, so a formatting problem can never lose generated code.
    """
    if not ruff_available():
        return source

    isort_args = [
        "ruff",
        "check",
        "--isolated",
        "--select",
        "I",
        "--fix-only",
        "--quiet",
    ]
    if first_party:
        isort_args += [
            "--config",
            f'lint.isort.known-first-party = ["{first_party}"]',
        ]
    isort_args += ["--stdin-filename", filename, "-"]
    sorted_imports = _run(isort_args, source)
    staged = sorted_imports if sorted_imports else source

    formatted = _run(
        [
            "ruff",
            "format",
            "--isolated",
            "--line-length",
            str(LINE_LENGTH),
            "--stdin-filename",
            filename,
            "-",
        ],
        staged,
    )
    return formatted if formatted else staged


def format_manifest_python(files: list, first_party: str = "") -> list[str]:
    """Format every Python file in a manifest in place.

    Returns the paths that changed, which the caller can report.
    """
    changed: list[str] = []
    if not ruff_available():
        return changed
    for generated in files:
        if not generated.path.endswith(".py"):
            continue
        formatted = format_python(
            generated.content, generated.path, first_party=first_party
        )
        if formatted != generated.content:
            generated.content = formatted
            changed.append(generated.path)
    return changed
