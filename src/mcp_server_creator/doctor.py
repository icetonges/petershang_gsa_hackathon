"""Preflight check, runnable before anyone connects a client.

    uv run mcp-a-creator-doctor
    uv run mcp-a-creator-doctor --environment gov_il5

The first gate is whether the thing starts at all. This answers that in one
command, in plain language, and exits non-zero only when something is
genuinely broken rather than merely unconfigured. Most of what it reports as
absent is optional, and it says so.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from mcp_server_creator import __version__, designs, environments, library
from mcp_server_creator.config import settings

OK = "  ok   "
INFO = " info  "
WARN = " warn  "
FAIL = " FAIL  "


def _line(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label}" + (f"\n         {detail}" if detail else ""))


def check_core() -> list[str]:
    """Everything that must work for the server to serve at all."""
    problems: list[str] = []

    try:
        from mcp_server_creator.app import mcp

        _line(OK, f"server imports ({mcp.name}, version {__version__})")
    except Exception as error:
        _line(FAIL, "server does not import", str(error))
        problems.append("the server does not import")
        return problems

    try:
        import fastmcp

        _line(OK, f"fastmcp {fastmcp.__version__}")
    except Exception as error:
        _line(FAIL, "fastmcp missing", str(error))
        problems.append("fastmcp is not installed")

    skills = library.skill_names()
    if len(skills) >= 7:
        _line(OK, f"{len(skills)} skills bundled", ", ".join(skills))
    elif skills:
        _line(WARN, f"only {len(skills)} skills found", ", ".join(skills))
    else:
        _line(FAIL, "no skills found", "the package data did not install")
        problems.append("the bundled skills are missing")

    docs = [entry["slug"] for entry in library.list_knowledge()]
    if docs:
        _line(OK, f"{len(docs)} knowledge documents", ", ".join(docs))
    else:
        _line(FAIL, "no knowledge documents found")
        problems.append("the bundled knowledge is missing")

    return problems


def _check_optional(profile: environments.EnvironmentProfile) -> None:
    """Report the optional capabilities, and say plainly that they are."""
    mode = designs.store_mode()
    if mode == "file":
        _line(OK, f"design store: file ({settings.creator_design_dir})")
    else:
        multi = (
            environments.availability(profile, "apps_multi_replica")
            is not environments.Availability.UNSUPPORTED
        )
        detail = designs.handle_advice(multi)
        _line(INFO if not multi else WARN, "design store: memory", detail)

    if settings.write_enabled:
        _line(OK, f"writing enabled, root {settings.creator_output_root}")
    else:
        _line(
            INFO,
            "writing to disk is off",
            "Optional. Set CREATOR_ALLOW_WRITE=true and CREATOR_OUTPUT_ROOT "
            "to let creator_write_project materialize a project.",
        )

    if shutil.which("ruff"):
        _line(OK, "ruff found: generated code will be formatted")
    else:
        _line(
            WARN,
            "ruff not found",
            "Generated projects will need 'uv run ruff format .' once before "
            "their own CI format check passes.",
        )

    if shutil.which("databricks"):
        _line(OK, "databricks CLI found")
    else:
        _line(
            INFO,
            "databricks CLI not found",
            "Optional here. Needed on whatever machine runs the deploy script.",
        )

    if settings.live_profiling_available:
        _line(OK, "live Unity Catalog inspection is configured")
    else:
        missing = [
            name
            for name, present in (
                ("DATABRICKS_HOST", bool(settings.databricks_host)),
                ("DATABRICKS_WAREHOUSE_ID", bool(settings.databricks_warehouse_id)),
                (
                    "a credential",
                    bool(
                        settings.databricks_token
                        or (
                            settings.databricks_client_id
                            and settings.databricks_client_secret
                        )
                    ),
                ),
            )
            if not present
        ]
        _line(
            INFO,
            "live table inspection off",
            "Optional. Paste a schema instead. Unset: " + ", ".join(missing),
        )


def _report_environment(profile: environments.EnvironmentProfile) -> None:
    """Print the capability picture for the target environment."""
    print(f"\nTarget environment: {profile.value}")
    fit = environments.source_kind_fit(profile)
    print(f"  data sources usable here : {', '.join(fit['usable']) or 'none'}")
    if fit["verify_first"]:
        print(f"  verify before designing  : {', '.join(fit['verify_first'])}")
    if fit["blocked"]:
        print(f"  blocked here             : {', '.join(fit['blocked'])}")
    for note in environments.notes(profile):
        print(f"  - {note}")


def main(argv: list[str] | None = None) -> int:
    """Run the preflight check and return a process exit code.

    `argv` is accepted so this can be called directly, including from a
    test, without argparse reading the host process's arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment",
        default=environments.EnvironmentProfile.UNKNOWN.value,
        choices=[profile.value for profile in environments.EnvironmentProfile],
        help="Which Databricks environment the generated servers will target.",
    )
    args = parser.parse_args(argv)
    profile = environments.EnvironmentProfile(args.environment)

    print("MCP A Creator: preflight\n")
    problems = check_core()
    print()
    _check_optional(profile)
    _report_environment(profile)

    print()
    if problems:
        print("NOT READY:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nFix those, then run this again.")
        return 1
    print("Ready. Connect a client with:")
    print('  {"mcpServers": {"mcp-a-creator": {"command": "uv",')
    print('    "args": ["run", "mcp-a-creator"], "cwd": "<this repo>"}}}')
    print("\nThen say: I want to build an MCP server over my data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
