"""Regression tests for the fixes made after the Semgrep scan.

Two findings were real: a crafted field name could inject Python into a
generated tool module, and grants.sql pasted UC names unquoted. The rest
were SQL built from allowlisted, backtick-quoted identifiers with bound
values; those lines now carry `# nosemgrep`, and the generated code keeps
the same marker so generated servers scan clean too.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_server_creator.config import Settings
from mcp_server_creator.generators import generate_full_project
from mcp_server_creator.generators.databricks import grants_sql

PAYLOAD = 'x", COLUMNS); import os; os.system("id"); ("'


def _tool_modules(manifest):
    return [
        f
        for f in manifest.files
        if f.path.endswith(".py") and "/tools/" in f.path and "__init__" not in f.path
    ]


def test_crafted_field_name_stays_a_string_literal(designed_spec):
    spec = designed_spec.model_copy(deep=True)
    for source in spec.data_sources:
        for field in source.fields:
            if field.role.value in {"measure", "dimension", "identifier", "date"}:
                old = field.name
                field.name = PAYLOAD
                for tool in spec.tools:
                    for param in tool.params:
                        if param.source_field == old:
                            param.source_field = PAYLOAD
    manifest = generate_full_project(spec)
    modules = _tool_modules(manifest)
    assert modules
    for module in modules:
        tree = ast.parse(module.content)  # must still be valid Python
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "system"
        ]
        assert not calls, f"{module.path} gained executable code"


def test_grants_quote_names_and_skip_unsafe_ones(designed_spec):
    content = grants_sql(designed_spec)
    assert "GRANT USE CATALOG ON CATALOG `main`" in content

    spec = designed_spec.model_copy(deep=True)
    table = next(s for s in spec.data_sources if s.kind.value == "uc_table")
    table.table = "awards; DROP TABLE x"
    content = grants_sql(spec)
    assert "DROP TABLE" not in content
    assert "Skipped" in content


def test_generated_sql_lines_carry_the_scanner_marker(designed_spec):
    manifest = generate_full_project(designed_spec)
    sql_lines = [
        line
        for module in _tool_modules(manifest)
        for line in module.content.splitlines()
        if line.strip().startswith(('f"SELECT', '"SELECT'))
    ]
    assert sql_lines
    for line in sql_lines:
        assert "# nosemgrep" in line and "# nosec B608" in line, line
        assert len(line) <= 88, line  # the generated CI runs ruff E501


def _write(manifest, root: Path) -> Path:
    for generated in manifest.files:
        target = root / generated.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generated.content)
    return root


@pytest.mark.skipif(shutil.which("bandit") is None, reason="bandit not installed")
def test_generated_project_is_bandit_clean(designed_spec, tmp_path):
    root = _write(generate_full_project(designed_spec), tmp_path)
    result = subprocess.run(
        ["bandit", "-c", "pyproject.toml", "-r", ".", "-q", "-f", "json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    findings = json.loads(result.stdout or "{}").get("results", [])
    assert not findings, [
        (f["test_id"], f["filename"], f["line_number"]) for f in findings
    ]


def test_generated_local_http_binds_loopback(designed_spec):
    manifest = generate_full_project(designed_spec)
    config = next(f.content for f in manifest.files if f.path.endswith("/config.py"))
    assert 'mcp_host: str = "127.0.0.1"' in config


def test_secrets_are_hidden_from_repr():
    settings = Settings(databricks_client_secret="s3cr3t", databricks_token="dapi1")
    text = repr(settings)
    assert "s3cr3t" not in text
    assert "dapi1" not in text
