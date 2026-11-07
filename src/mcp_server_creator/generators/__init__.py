"""Code generators: specification in, complete project out.

`generate_full_project` is the usual entry point. The individual generators
are exposed so a caller can regenerate one layer, such as the deployment kit
after moving a table, without rewriting the whole project.
"""

from __future__ import annotations

from mcp_server_creator.generators import (
    dataaccess,
    databricks,
    evaluation,
    formatting,
    project,
    toolcode,
)
from mcp_server_creator.models import FileManifest, ServerSpec

__all__ = [
    "dataaccess",
    "databricks",
    "evaluation",
    "formatting",
    "generate_full_project",
    "project",
    "toolcode",
]


def generate_full_project(
    spec: ServerSpec,
    include_evaluation: bool = True,
    include_deployment: bool = True,
) -> FileManifest:
    """Generate the whole project: server, deployment kit, and evaluation."""
    manifest = project.generate_project(spec)
    if include_deployment:
        manifest.merge(databricks.generate_deployment(spec))
    if include_evaluation:
        manifest.merge(evaluation.generate_evaluation(spec))
        _add_eval_dependency_group(manifest, spec)

    # Format before the files leave the generator, so the project passes its
    # own `ruff check` and `ruff format --check` on the first commit.
    formatting.format_manifest_python(manifest.files, first_party=spec.package_name)
    if not formatting.ruff_available():
        manifest.warnings.append(
            "ruff was not available, so the generated Python was not "
            "formatted. Run 'uv run ruff format .' in the new project "
            "before the first commit, or its CI format check will fail."
        )

    manifest.next_steps = [
        "Write the manifest to disk.",
        "uv sync && uv run ruff format . && uv run pytest tests/ -v",
        "Connect a local client over stdio and ask one real question.",
        "Fill in the golden answers via eval/answer_key.py, then run the "
        "--no-tools baseline followed by the tooled run.",
        "Deploy with deploy/databricks/deploy.sh, then apply grants.sql to "
        "the app service principal.",
    ]
    return manifest


def _add_eval_dependency_group(manifest: FileManifest, spec: ServerSpec) -> None:
    """Fold the evaluation dependencies into the generated pyproject."""
    for generated in manifest.files:
        if generated.path != "pyproject.toml":
            continue
        extras = "\n".join(f'    "{dep}",' for dep in evaluation.eval_dependencies())
        updated = generated.content.replace(
            '    "ruff>=0.6.0",\n]',
            '    "ruff>=0.6.0",\n' + extras + "\n]",
        )
        manifest.add(
            "pyproject.toml",
            updated,
            "Packaging, lint, test, and evaluation dependencies",
        )
        return
    manifest.warnings.append(
        f"Could not fold evaluation dependencies into pyproject.toml for "
        f"{spec.server_name}; add them by hand."
    )
