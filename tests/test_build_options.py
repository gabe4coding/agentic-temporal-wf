from src.agents.pr_fixer import build_options


def test_build_options_uses_sdk_sandbox(monkeypatch):
    monkeypatch.setenv("RUN_CAPABILITY_TOKEN", "opaque-run-token")
    monkeypatch.setenv("CAPABILITY_MCP_URL", "http://capability-broker:8443/mcp")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://capability-broker:8443/anthropic")
    opts = build_options()
    # Pattern-C: no more bypassPermissions.
    assert opts.permission_mode != "bypassPermissions"
    # SDK-native sandbox block must be present and enabled.
    # SandboxSettings is a TypedDict (camelCase keys).
    sandbox = getattr(opts, "sandbox", None)
    assert sandbox is not None
    assert sandbox["enabled"] is True
    assert sandbox["network"]["allowedDomains"]
    # Bash / Write / WebFetch explicitly disallowed.
    for forbidden in ("Bash", "Write", "WebFetch"):
        assert forbidden in (opts.disallowed_tools or [])
    assert opts.mcp_servers["capability"]["type"] == "http"
    assert opts.env["ANTHROPIC_API_KEY"] == "opaque-run-token"
    assert opts.env["ANTHROPIC_BASE_URL"].endswith("/anthropic")
