"""Remote MCP configuration exposes only the opaque run capability."""

from src.tools.github_mcp import build_github_mcp_config


def test_build_github_mcp_is_remote_and_uses_opaque_token():
    cfg = build_github_mcp_config("http://broker:8443/mcp", "run-opaque-token")
    assert cfg == {
        "type": "http",
        "url": "http://broker:8443/mcp",
        "headers": {"Authorization": "Bearer run-opaque-token"},
    }


def test_build_github_mcp_uses_env_default(monkeypatch):
    monkeypatch.setenv("CAPABILITY_MCP_URL", "http://from-env:9000/mcp")
    monkeypatch.setenv("RUN_CAPABILITY_TOKEN", "opaque")
    config = build_github_mcp_config()
    assert config["url"] == "http://from-env:9000/mcp"
    assert config["headers"]["Authorization"] == "Bearer opaque"
