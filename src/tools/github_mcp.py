import os
from typing import Any


def build_github_mcp_config() -> dict[str, Any]:
    """Build the mcp_servers["github"] config for ClaudeAgentOptions.

    Wraps the official github/github-mcp-server Go binary (installed in
    the worker image, see Dockerfile). Reads GITHUB_TOKEN at call time so
    importing this module without a token does not crash unit tests of
    other modules.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return {
        "command": "github-mcp-server",
        "args": ["stdio"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }
