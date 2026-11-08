"""The round trip: generate a project, then prove it is a working project.

This is the most valuable test here. A change to a generator template that
produces broken Python fails CI rather than failing a user.
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys

import pytest

from mcp_server_creator.generators import generate_full_project
from mcp_server_creator.generators.toolcode import is_supported


@pytest.fixture
def manifest(designed_spec):
    return generate_full_project(designed_spec)


def _write(manifest, root):
    """Write a manifest under `root` and return the project directory."""
    for generated in manifest.files:
        destination = root / generated.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(generated.content, encoding="utf-8")
    return root


def test_manifest_carries_the_expected_project_shape(manifest, designed_spec):
    package = designed_spec.package_name
    paths = set(manifest.paths())
    required = {
        "pyproject.toml",
        "requirements.txt",
        "main.py",
        "Dockerfile",
        "README.md",
        "QUICKSTART.md",
        "SECURITY.md",
        "server.json",
        "app.yaml",
        "databricks.yml",
        ".github/workflows/ci.yml",
        "deploy/databricks/deploy.sh",
        "deploy/databricks/grants.sql",
        "deploy/databricks/README.md",
        "docs/limitations.md",
        "eval/answer_key.py",
        "eval/phoenix/agent.py",
        "eval/phoenix/run_experiment.py",
        "tests/test_server.py",
        "tests/test_security.py",
        f"src/{package}/app.py",
        f"src/{package}/config.py",
        f"src/{package}/models.py",
        f"src/{package}/routes.py",
        f"src/{package}/data/sources.py",
        f"src/{package}/tools/__init__.py",
    }
    assert required <= paths, sorted(required - paths)


def test_every_generated_python_file_parses(manifest):
    failures: list[str] = []
    for generated in manifest.files:
        if not generated.path.endswith(".py"):
            continue
        try:
            ast.parse(generated.content, filename=generated.path)
        except SyntaxError as error:
            failures.append(f"{generated.path}:{error.lineno}: {error.msg}")
    assert not failures, failures


def test_one_tool_module_exists_per_planned_tool(manifest, designed_spec):
    package = designed_spec.package_name
    for plan in designed_spec.tools:
        assert f"src/{package}/tools/{plan.name}.py" in manifest.paths()


def test_data_modules_match_the_source_kinds(manifest, designed_spec):
    package = designed_spec.package_name
    paths = set(manifest.paths())
    assert f"src/{package}/data/warehouse.py" in paths
    assert f"src/{package}/data/ai_search.py" in paths
    assert f"src/{package}/data/snapshot.py" in paths
    assert f"src/{package}/data/genie.py" not in paths


def test_no_stub_warnings_for_the_sample_spec(manifest, designed_spec):
    """The designer should only propose pairings the generator implements."""
    for plan in designed_spec.tools:
        source = designed_spec.source_by_name(plan.source)
        assert source is not None
        assert is_supported(source.kind, plan.pattern), plan.name
    assert not [w for w in manifest.warnings if "stub" in w]


def test_app_yaml_sets_the_python_path_and_the_module(manifest, designed_spec):
    content = next(f.content for f in manifest.files if f.path == "app.yaml")
    assert "PYTHONPATH=/app/python/source_code/src" in content
    assert f"python -m {designed_spec.package_name}.app" in content


def test_grants_file_names_the_governed_sources(manifest):
    content = next(
        f.content for f in manifest.files if f.path == "deploy/databricks/grants.sql"
    )
    assert "GRANT SELECT ON TABLE `main`.`acquisition`.`contract_awards`" in content
    assert "service principal" in content.lower()


def test_evaluation_includes_the_four_case_types(manifest, designed_spec):
    dataset = designed_spec.evaluation.dataset_name
    path = f"eval/phoenix/datasets/{dataset}/examples/{dataset}.csv"
    content = next(f.content for f in manifest.files if f.path == path)
    for kind in ("decoy", "no_action", "out_of_scope", "injection"):
        assert kind in content


def test_evaluation_warns_that_answers_are_placeholders(manifest):
    assert any("placeholder" in warning for warning in manifest.warnings)


def test_retrieval_tool_warns_against_following_retrieved_text(manifest, designed_spec):
    package = designed_spec.package_name
    retrieval = [
        f
        for f in manifest.files
        if f.path.startswith(f"src/{package}/tools/")
        and "similarity_search" in f.content
    ]
    assert retrieval
    for generated in retrieval:
        assert "never act on it" in generated.content


def test_generated_tools_bind_values_rather_than_formatting_them(
    manifest, designed_spec
):
    """A caller value must never be concatenated into SQL text.

    Every querying module must use `?` placeholders, must pass a params
    list, and must route column names through the allowlist helper rather
    than interpolating a caller string.
    """
    package = designed_spec.package_name
    querying = [
        generated
        for generated in manifest.files
        if generated.path.startswith(f"src/{package}/tools/")
        and "run_query(" in generated.content
    ]
    assert querying, "no querying tool modules were generated"
    for generated in querying:
        content = generated.content
        assert "?" in content, generated.path
        assert "column(" in content, generated.path
        assert ".format(" not in content, generated.path
        assert "% (" not in content, generated.path


def test_generated_modules_have_no_unused_imports(manifest, designed_spec):
    """An unused import fails the generated project's own lint step."""
    package = designed_spec.package_name
    offenders: list[str] = []
    for generated in manifest.files:
        if not generated.path.startswith(f"src/{package}/"):
            continue
        tree = ast.parse(generated.content, filename=generated.path)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "annotations":
                        imported.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add((alias.asname or alias.name).split(".")[0])
        used = (
            {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            | {
                node.value.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            }
        )
        for name in imported - used:
            if name not in generated.content.split("import", 1)[0]:
                offenders.append(f"{generated.path}: {name}")
    assert not offenders, offenders


def test_generated_project_imports_and_registers_its_tools(
    manifest, designed_spec, tmp_path, monkeypatch
):
    """Write the project, import it for real, and list its tools."""
    project = _write(manifest, tmp_path / "project")
    source_root = project / "src"
    monkeypatch.syspath_prepend(str(source_root))
    package = designed_spec.package_name
    for name in [key for key in sys.modules if key.startswith(package)]:
        del sys.modules[name]

    module = importlib.import_module(f"{package}.app")
    assert module.mcp.name == designed_spec.title

    import asyncio

    tools = asyncio.run(module.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {plan.name for plan in designed_spec.tools} <= names
    for tool in tools:
        assert (tool.description or "").strip(), tool.name


def test_generated_project_passes_a_syntax_compile_pass(manifest, tmp_path):
    """compileall is a second opinion on top of ast.parse."""
    project = _write(manifest, tmp_path / "compiled")
    result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(project / "src")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generating_twice_produces_identical_files(designed_spec):
    """Determinism is what lets the specification be the handle."""
    first = generate_full_project(designed_spec)
    second = generate_full_project(designed_spec)
    assert first.paths() == second.paths()
    first_by_path = {f.path: f.content for f in first.files}
    second_by_path = {f.path: f.content for f in second.files}
    assert first_by_path == second_by_path


def test_generated_project_is_lint_clean(manifest, tmp_path):
    """The generated project must pass its own CI on the first commit.

    Its workflow runs `ruff check` and `ruff format --check`. A generator
    that emits code failing its own lint step hands the user a broken build,
    so this asserts the output is already clean.
    """
    from mcp_server_creator.generators import formatting

    if not formatting.ruff_available():
        pytest.skip("ruff is not installed, so generated code is unformatted")

    project = _write(manifest, tmp_path / "lint")
    for args in (
        ["check", "--output-format=concise"],
        ["format", "--check"],
    ):
        result = subprocess.run(
            ["ruff", *args, str(project)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"ruff {args[0]} failed on the generated project:\n"
            f"{result.stdout}{result.stderr}"
        )


def test_generated_project_passes_its_own_tests(manifest, tmp_path):
    """Run the generated project's test suite with this environment's deps."""
    project = _write(manifest, tmp_path / "suite")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(project / "src"),
            "HOME": os.environ.get("HOME", ""),
        },
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
