"""Guided workflows, surfaced in a client as slash commands.

Each one puts the model on the right path with the right skill loaded, which
matters because the most common failure is starting to write code before the
discovery questions are answered.
"""

from __future__ import annotations

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register the workflow prompts with the MCP server."""

    @mcp.prompt()
    def create_mcp_server(what_i_want: str = "", my_data: str = "") -> str:
        """Build a custom MCP server on Databricks, end to end.

        Args:
            what_i_want: What the server should let an agent do.
            my_data: Where the data lives, or a pasted schema.
        """
        lines = [
            "I want to build my own MCP server.",
            "",
            f"What it should do: {what_i_want or '(I will describe this)'}",
            f"My data: {my_data or '(I will describe this)'}",
            "",
            "Work through this properly:",
            "1. Load the mcp-server-creator skill with creator_load_document "
            "and follow its six phases.",
            "2. Run discovery first. Ask me the questions you cannot infer, "
            "especially whose job this is and what questions they ask today "
            "in their own words. Do not write code yet.",
            "3. Profile each of my data sources and tell me plainly what the "
            "data cannot support.",
            "4. Propose the tool surface and get my agreement on the names "
            "before generating anything.",
            "5. Review the design, fix the blockers, then generate.",
            "6. Give me the deployment steps and the grants I will need.",
        ]
        return "\n".join(lines)

    @mcp.prompt()
    def review_my_mcp_server(server_description: str = "") -> str:
        """Review an existing MCP server design against the rubric.

        Args:
            server_description: The server's tools, data, and purpose.
        """
        return "\n".join(
            [
                "Review my MCP server design.",
                "",
                f"Here it is: {server_description or '(I will paste it)'}",
                "",
                "Load the mcp-tool-design and mcp-security-review skills, "
                "build a ServerSpec that matches what I described, then run "
                "creator_review_server on it.",
                "",
                "Report the blockers first. For each finding, tell me the "
                "concrete change, not the principle. Be direct about what is "
                "weak: I would rather hear it now than after deployment.",
            ]
        )

    @mcp.prompt()
    def mcp_from_my_data(schema_or_location: str = "") -> str:
        """Start from the data and work out what server is worth building.

        Args:
            schema_or_location: A table name, a pasted schema, or a corpus.
        """
        return "\n".join(
            [
                "I have data and I am not sure what MCP server to build from it.",
                "",
                f"The data: {schema_or_location or '(I will paste it)'}",
                "",
                "Profile it with creator_profile_data_source, then tell me:",
                "- what question shapes it actually supports,",
                "- what it cannot answer, and what is missing to fix that,",
                "- the three or four tools you would build, and who each one is for.",
                "",
                "Then ask me whether that matches a job anyone actually does. "
                "If it does not, say so rather than building it.",
            ]
        )
