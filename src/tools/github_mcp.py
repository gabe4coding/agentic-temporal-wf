"""GitHub MCP server config builder.

Pattern-C: the sandbox does NOT carry a long-lived GitHub PAT in its
env. The PAT lives on the credential proxy (`/__token/github`). This
helper fetches it at iteration start and hands it to the
github-mcp-server child process via env. The token is rotated on every
iteration (per-iteration short-lived fetch — see the proxy docstring).

The GitHub App installation-token upgrade (1h TTL) is Open Question #2
in the plan.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


def _fetch_github_token(proxy_url: str) -> str:
    r = httpx.get(f"{proxy_url}/__token/github", timeout=5.0)
    r.raise_for_status()
    return r.json()["token"]


def build_github_mcp_config(proxy_url: str | None = None) -> dict[str, Any]:
    """Build the `mcp_servers["github"]` config.

    `proxy_url` defaults to `$CREDENTIAL_PROXY_URL` (compose-resolved) so
    callers can omit it in normal operation and only override in tests.
    """
    proxy = proxy_url or os.environ.get(
        "CREDENTIAL_PROXY_URL", "http://credential-proxy:8443"
    )
    token = _fetch_github_token(proxy)
    return {
        "command": "github-mcp-server",
        "args": ["stdio"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }
