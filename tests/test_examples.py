"""The starter examples must name data that exists, and say whose tool it is."""

from __future__ import annotations

from pathlib import Path

from mcp_server_creator import examples
from mcp_server_creator.app import mcp

ROOT = Path(__file__).resolve().parents[1]


def test_every_prompt_names_the_creator():
    for prompt in examples.prompts():
        assert prompt.startswith("Use MCP A Creator"), prompt


def test_examples_cover_several_people():
    personas = {e["persona"] for e in examples.EXAMPLES}
    assert len(personas) >= 4


def test_concrete_tables_are_samples_tables_named_in_the_prompt():
    for e in examples.EXAMPLES:
        for table in e["tables"]:
            assert table.startswith("samples."), table
            assert table in e["prompt"], table


def test_own_table_example_is_a_placeholder_and_checks_existence():
    own = [e for e in examples.EXAMPLES if not e["tables"]]
    assert own, "keep one bring-your-own-table template"
    for e in own:
        assert "<catalog.schema." in e["prompt"]
        assert "confirm the table exists" in e["prompt"]


def test_no_example_points_at_a_table_that_does_not_exist():
    for path in [
        ROOT / "README.md",
        ROOT / "src/mcp_server_creator/app.py",
        ROOT / "src/mcp_server_creator/routes.py",
        ROOT / "src/mcp_server_creator/examples.py",
    ]:
        assert "main.contracts" not in path.read_text(), path


def test_instructions_carry_the_examples_and_the_existence_check():
    text = mcp.instructions or ""
    for prompt in examples.prompts():
        assert prompt in text
    assert "Confirm every table exists" in text
