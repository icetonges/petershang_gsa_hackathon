"""Live profiling runs as the calling user when Databricks Apps forwards a token.

The forwarded token is the user's own downscoped credential, so profiling
with it can read exactly what Unity Catalog lets that person read. The app's
service principal is only the fallback.
"""

from __future__ import annotations

import pytest

from mcp_server_creator import live
from mcp_server_creator.config import settings


@pytest.fixture
def configured(monkeypatch):
    # The SDK reads ambient credentials; keep a notebook's token out of it.
    for name in ("DATABRICKS_TOKEN", "DATABRICKS_CONFIG_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        settings, "databricks_host", "https://example.cloud.databricks.com"
    )
    monkeypatch.setattr(settings, "databricks_warehouse_id", "abc123")
    monkeypatch.setattr(settings, "databricks_client_id", "sp-id")
    monkeypatch.setattr(settings, "databricks_client_secret", "sp-secret")
    monkeypatch.setattr(settings, "databricks_token", "")
    monkeypatch.setattr(settings, "creator_profile_as_user", True)
    monkeypatch.setattr(settings, "creator_enable_live_profiling", True)


def _forward(monkeypatch, headers: dict[str, str]) -> None:
    import fastmcp.server.dependencies as dependencies

    monkeypatch.setattr(dependencies, "get_http_headers", lambda *a, **k: headers)


def test_forwarded_user_token_is_used_first(configured, monkeypatch):
    _forward(monkeypatch, {"x-forwarded-access-token": "user-token"})
    kwargs = live.connection_kwargs()
    assert kwargs["access_token"] == "user-token"
    assert "credentials_provider" not in kwargs
    assert kwargs["server_hostname"] == "example.cloud.databricks.com"
    assert kwargs["http_path"] == "/sql/1.0/warehouses/abc123"
    assert live.identity() == live.USER_IDENTITY


def test_without_a_forwarded_token_the_app_identity_is_used(configured, monkeypatch):
    _forward(monkeypatch, {})
    kwargs = live.connection_kwargs()
    assert "access_token" not in kwargs
    assert callable(kwargs["credentials_provider"])
    assert live.identity() == live.APP_IDENTITY


def test_profiling_as_the_user_can_be_turned_off(configured, monkeypatch):
    _forward(monkeypatch, {"x-forwarded-access-token": "user-token"})
    monkeypatch.setattr(settings, "creator_profile_as_user", False)
    assert live.caller_token() == ""
    assert "access_token" not in live.connection_kwargs()


def test_outside_a_request_there_is_no_caller_token(configured):
    assert live.caller_token() == ""


def test_a_user_token_alone_makes_live_profiling_available(configured, monkeypatch):
    monkeypatch.setattr(settings, "databricks_client_id", "")
    monkeypatch.setattr(settings, "databricks_client_secret", "")
    _forward(monkeypatch, {})
    assert not live.available()
    _forward(monkeypatch, {"x-forwarded-access-token": "user-token"})
    assert live.available()


def test_the_token_never_appears_in_an_error(configured, monkeypatch):
    _forward(monkeypatch, {"x-forwarded-access-token": "secret-user-token"})
    monkeypatch.setattr(settings, "databricks_warehouse_id", "")
    with pytest.raises(live.LiveInspectionError) as error:
        live.connection_kwargs()
    assert "secret-user-token" not in str(error.value)


def test_a_failed_user_read_falls_back_to_the_app_and_says_why(configured, monkeypatch):
    _forward(monkeypatch, {"x-forwarded-access-token": "user-token"})
    attempts = []

    def fake_profile(catalog, schema, table, prefer_user):
        attempts.append(prefer_user)
        if prefer_user:
            raise live.LiveInspectionError("Could not connect (scope missing)")
        return [], {"identity": live.APP_IDENTITY}

    monkeypatch.setattr(live, "_profile_table", fake_profile)
    _, details = live.profile_table("samples", "accuweather", "forecast")
    assert attempts == [True, False]
    assert details["fallback"]["from"] == live.USER_IDENTITY
    assert "scope missing" in details["fallback"]["reason"]


def test_error_descriptions_redact_tokens():
    error = RuntimeError(
        "401 for Bearer eyJhbGciOiJIUzI1NiJ9.abc.def and dapi0123456789ab"
    )
    text = live.describe_error(error)
    assert "eyJ" not in text and "dapi0123" not in text
    assert text.startswith("RuntimeError: 401")
