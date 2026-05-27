from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import httpx

from src.models import CapabilityBinding, OperationResult
from src.proxy.credential_proxy import CapabilityRegistry, create_proxy_app


def _binding(**overrides) -> CapabilityBinding:
    values = {
        "workflow_id": "wf-1",
        "repository": "o/r",
        "pr_number": 7,
        "workspace_path": "/workspaces/autofix-wf-1/repo",
        "capabilities": {"model.invoke", "source.read", "changes.publish"},
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    values.update(overrides)
    return CapabilityBinding(**values)


def _client() -> tuple[TestClient, CapabilityRegistry]:
    registry = CapabilityRegistry()
    app = create_proxy_app(
        github_token="ghp_secret",
        anthropic_key="sk-ant-secret",
        registration_secret="register",
        registry=registry,
    )
    return TestClient(app), registry


def test_registration_requires_trusted_secret():
    client, _ = _client()
    response = client.post("/bindings", json={"token": "opaque-token-value-long-enough", "binding": _binding().model_dump(mode="json")})
    assert response.status_code == 403


def test_registration_is_fail_closed_without_configured_secret():
    app = create_proxy_app(github_token="g", anthropic_key="a")
    response = TestClient(app).post(
        "/bindings",
        headers={"X-Broker-Registration-Secret": ""},
        json={
            "token": "opaque-token-value-long-enough",
            "binding": _binding().model_dump(mode="json"),
        },
    )
    assert response.status_code == 503


def test_no_upstream_token_endpoint_is_available():
    client, _ = _client()
    assert client.get("/__token/github").status_code == 404
    assert client.get("/__token/anthropic").status_code == 404


def test_invalid_and_expired_tokens_are_denied():
    client, registry = _client()
    assert client.post("/mcp", headers={"Authorization": "Bearer missing"}, json={"method": "tools/list", "id": 1}).status_code == 401
    registry.register("expired-token-value-long-enough", _binding(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert client.post("/mcp", headers={"Authorization": "Bearer expired-token-value-long-enough"}, json={"method": "tools/list", "id": 1}).status_code == 401


def test_model_relay_replaces_opaque_token_with_trusted_key(monkeypatch):
    client, registry = _client()
    registry.register("opaque-token-value-long-enough", _binding())
    recorded = {}

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, content, headers, params):
            recorded.update({"method": method, "url": url, "headers": headers, "params": params})
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        "src.proxy.credential_proxy.httpx.AsyncClient",
        lambda **_kwargs: StubClient(),
    )
    response = client.post(
        "/anthropic/v1/messages?beta=true",
        headers={
            "x-api-key": "opaque-token-value-long-enough",
            "anthropic-beta": "tool-use-test",
        },
        json={"model": "claude"},
    )
    assert response.status_code == 200
    assert recorded["url"] == "https://api.anthropic.com/v1/messages"
    assert recorded["headers"]["x-api-key"] == "sk-ant-secret"
    assert recorded["headers"]["anthropic-beta"] == "tool-use-test"
    assert recorded["params"]["beta"] == "true"


def test_mcp_exposes_only_scoped_capabilities():
    client, registry = _client()
    registry.register("opaque-token-value-long-enough", _binding())
    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer opaque-token-value-long-enough"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
    )
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {
        "get_pr_context",
        "list_review_comments",
        "list_check_results",
        "request_push_changes",
    }


def test_mcp_reads_bound_pr_not_agent_supplied_repo(monkeypatch):
    client, registry = _client()
    registry.register("opaque-token-value-long-enough", _binding())
    paths = []

    async def fake_get(_token, path):
        paths.append(path)
        return {"number": 7}

    monkeypatch.setattr("src.proxy.credential_proxy._github_get", fake_get)
    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer opaque-token-value-long-enough"},
        json={"jsonrpc": "2.0", "method": "tools/call", "id": 2, "params": {"name": "get_pr_context", "arguments": {"repository": "evil/repo", "number": 99}}},
    )
    assert response.status_code == 200
    assert paths == ["/repos/o/r/pulls/7"]


def test_request_push_is_forwarded_to_bound_workflow(monkeypatch):
    client, registry = _client()
    registry.register("opaque-token-value-long-enough", _binding())
    requests = []

    async def fake_update(workflow_id, request):
        requests.append((workflow_id, request))
        return OperationResult(operation_key="key", status="pushed", external_result_id="sha")

    monkeypatch.setattr("src.proxy.credential_proxy._request_push_update", fake_update)
    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer opaque-token-value-long-enough"},
        json={"jsonrpc": "2.0", "method": "tools/call", "id": 3, "params": {"name": "request_push_changes", "arguments": {"summary": "done", "commit_message": "fix"}}},
    )
    assert response.status_code == 200
    assert requests[0][0] == "wf-1"
