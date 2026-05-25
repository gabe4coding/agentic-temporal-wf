import os

from pydantic_ai.mcp import MCPServerStdio


def build_github_mcp_server() -> MCPServerStdio:
    """Construct the GitHub MCP server. Reads GITHUB_TOKEN at call time."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return MCPServerStdio(
        "npx",
        args=["-y", "@github/github-mcp-server"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": token},
        id="github",
        timeout=15,
    )
