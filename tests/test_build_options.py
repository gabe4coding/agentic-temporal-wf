from src.agents.pr_fixer import build_options


def test_build_options_uses_sdk_sandbox(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("CREDENTIAL_PROXY_URL", "http://credential-proxy:8443")
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
