import httpx

from src.agents.pr_fixer import build_options


class _StubResponse:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:  # pragma: no cover — happy path only
        return None

    def json(self):
        return self._payload


def test_build_options_uses_sdk_sandbox(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("CREDENTIAL_PROXY_URL", "http://credential-proxy:8443")
    # Phase 4.3: build_github_mcp_config() now does a real GET against
    # the credential proxy. Stub it for this unit test — the proxy itself
    # is exercised in tests/test_credential_proxy.py and the http call
    # in tests/test_github_mcp.py.
    monkeypatch.setattr(
        httpx, "get", lambda *_a, **_kw: _StubResponse({"token": "ghp_x", "ttl_s": 60})
    )
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
