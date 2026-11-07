"""Server configuration loaded from environment variables or a .env file.

Every field can be overridden by an environment variable of the same name.
Copy `.env.example` to `.env` for local development.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """MCP server settings. Override any field via environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Transport ─────────────────────────────────────────────────────────────
    # app.py auto-selects HTTP when Databricks Apps injects DATABRICKS_APP_PORT
    # (or a generic PORT), so this rarely needs setting on a host.
    mcp_transport: str = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    # ── Writing generated projects to disk ────────────────────────────────────
    # The generator returns a file manifest by default. Writing to disk is a
    # side effect, so it is off unless an operator opts in AND sets a root.
    # Every path is resolved inside creator_output_root; traversal is refused.
    creator_allow_write: bool = False
    creator_output_root: str = ""
    creator_max_manifest_bytes: int = 4_000_000

    # ── Design handles ────────────────────────────────────────────────────────
    # A specification is about 17 KB of nested JSON. The server keeps it and
    # hands the model a short handle instead.
    #
    # "memory" is correct only when one replica serves every request.
    # Databricks Apps supports horizontal scaling, so use "file" with a
    # directory every replica can read when deploying there.
    creator_design_store: str = "memory"
    creator_design_dir: str = ""

    # ── Optional live data profiling ──────────────────────────────────────────
    # When the server runs as a Databricks App, Databricks injects
    # DATABRICKS_HOST / DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET, and
    # app.yaml can bind a warehouse id. With those present, profiling can
    # inspect a real Unity Catalog table instead of parsing pasted DDL.
    creator_enable_live_profiling: bool = True
    databricks_host: str = ""
    databricks_warehouse_id: str = ""
    databricks_client_id: str = ""
    databricks_client_secret: str = ""
    databricks_token: str = ""

    @property
    def live_profiling_available(self) -> bool:
        """True when a Unity Catalog table can actually be inspected."""
        if not self.creator_enable_live_profiling:
            return False
        if not (self.databricks_host and self.databricks_warehouse_id):
            return False
        has_oauth = bool(self.databricks_client_id and self.databricks_client_secret)
        return has_oauth or bool(self.databricks_token)

    @property
    def write_enabled(self) -> bool:
        """True when generated files may be written to the local filesystem."""
        return bool(self.creator_allow_write and self.creator_output_root)


settings = Settings()
