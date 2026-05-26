"""Verifies that build_github_mcp_config fetches the PAT from the
credential proxy and stamps it into the MCP server's env.

Pattern-C: GITHUB_TOKEN no longer lives in the sandbox env. The token is
fetched per iteration from http://credential-proxy:8443/__token/github."""
from __future__ import annotations

import httpx
import pytest

from src.tools.github_mcp import build_github_mcp_config


class _StubResponse:
    def __init__(self, json_body, status: int = 200) -> None:
        self._json = json_body
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=None, response=None  # type: ignore[arg-type]
            )

    def json(self):
        return self._json


def test_build_github_mcp_calls_proxy(monkeypatch):
    calls: list[str] = []

    def fake_get(url, *_, **__):
        calls.append(url)
        return _StubResponse({"token": "ghp_xxx", "ttl_s": 60})

    monkeypatch.setattr(httpx, "get", fake_get)
    cfg = build_github_mcp_config("http://proxy:8443")
    assert calls == ["http://proxy:8443/__token/github"]
    assert cfg == {
        "command": "github-mcp-server",
        "args": ["stdio"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"},
    }


def test_build_github_mcp_uses_env_default(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_PROXY_URL", "http://from-env:9000")

    captured = {}

    def fake_get(url, *_, **__):
        captured["url"] = url
        return _StubResponse({"token": "tok", "ttl_s": 1})

    monkeypatch.setattr(httpx, "get", fake_get)
    build_github_mcp_config()
    assert captured["url"] == "http://from-env:9000/__token/github"
