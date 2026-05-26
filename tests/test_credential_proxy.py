import os

import httpx
import pytest
from fastapi.testclient import TestClient

from src.proxy.credential_proxy import create_proxy_app


def test_proxy_injects_github_pat(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    app = create_proxy_app(
        github_token=os.environ["GITHUB_TOKEN"],
        anthropic_key="sk-ant-anth",
        allowed_hosts={"api.github.com"},
    )
    r = TestClient(app).post(
        "/__inject_test",
        json={"host": "api.github.com", "method": "GET", "path": "/repos/x/y"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["injected"]["authorization"].startswith("Bearer ghp_secret123")
    assert body["allowed"] is True


def test_proxy_denies_unknown_host():
    app = create_proxy_app(
        github_token="t", anthropic_key="k", allowed_hosts={"api.github.com"}
    )
    r = TestClient(app).post(
        "/__inject_test",
        json={"host": "evil.example", "method": "GET", "path": "/x"},
    )
    assert r.status_code == 403
