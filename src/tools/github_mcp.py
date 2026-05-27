"""Remote capability MCP configuration for the untrusted agent runtime."""
from __future__ import annotations

import os
from typing import Any


def build_github_mcp_config(
    broker_url: str | None = None, token: str | None = None
) -> dict[str, Any]:
    """Return SDK HTTP MCP configuration authenticated with an opaque run token."""
    url = broker_url or os.environ.get(
        "CAPABILITY_MCP_URL", "http://capability-broker:8443/mcp"
    )
    run_token = token or os.environ["RUN_CAPABILITY_TOKEN"]
    return {
        "type": "http",
        "url": url,
        "headers": {"Authorization": f"Bearer {run_token}"},
    }
