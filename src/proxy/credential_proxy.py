"""Trusted capability broker and Anthropic relay for untrusted sandboxes.

The broker holds GitHub and Anthropic credentials. A sandbox gets only a
random run token registered by the trusted provisioner. The token resolves to
one workflow and PR, so MCP arguments cannot redirect reads or publication.
"""
from __future__ import annotations

from datetime import UTC, datetime
import os
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response

from src.models import CapabilityBinding, OperationRequest


class CapabilityRegistry:
    def __init__(self) -> None:
        self._bindings: dict[str, CapabilityBinding] = {}

    def register(self, token: str, binding: CapabilityBinding) -> None:
        self._bindings[token] = binding

    def resolve(self, token: str, capability: str) -> CapabilityBinding:
        binding = self._bindings.get(token)
        if binding is None or binding.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=401, detail="invalid or expired run capability")
        if capability not in binding.capabilities:
            raise HTTPException(status_code=403, detail=f"capability {capability} not permitted")
        return binding


def _run_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    return token or request.headers.get("x-api-key", "") or request.headers.get("x-run-capability-token", "")


async def _github_get(github_token: str, path: str) -> Any:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"https://api.github.com{path}",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        response.raise_for_status()
        return response.json()


async def _request_push_update(workflow_id: str, request: OperationRequest):
    from temporalio.client import Client

    client = await Client.connect(os.environ["TEMPORAL_TARGET"])
    return await client.get_workflow_handle(workflow_id).execute_update(
        "request_push_changes", request
    )


def create_proxy_app(
    *,
    github_token: str,
    anthropic_key: str,
    allowed_hosts: set[str] | None = None,
    registration_secret: str = "",
    registry: CapabilityRegistry | None = None,
) -> FastAPI:
    """Create the trusted broker app.

    `allowed_hosts` remains accepted for compatibility with older deployment
    wiring; broker endpoints expose only fixed upstream destinations.
    """
    del allowed_hosts
    app = FastAPI(title="agent-temporal capability broker")
    bindings = registry or CapabilityRegistry()

    @app.post("/bindings", status_code=201)
    async def register_binding(
        request: Request,
        x_broker_registration_secret: str = Header(default=""),
    ):
        if not registration_secret:
            raise HTTPException(status_code=503, detail="broker registration not configured")
        if x_broker_registration_secret != registration_secret:
            raise HTTPException(status_code=403, detail="registration denied")
        body = await request.json()
        token = body.get("token", "")
        if len(token) < 20:
            raise HTTPException(status_code=400, detail="invalid opaque token")
        bindings.register(token, CapabilityBinding.model_validate(body["binding"]))
        return {"registered": True}

    @app.api_route("/anthropic/{path:path}", methods=["POST", "GET"])
    async def anthropic_relay(path: str, request: Request):
        bindings.resolve(_run_token(request), "model.invoke")
        if not (path == "v1/messages" or path.startswith("v1/messages/")):
            raise HTTPException(status_code=404, detail="unsupported Anthropic path")
        headers = {
            "x-api-key": anthropic_key,
            "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
            "content-type": request.headers.get("content-type", "application/json"),
        }
        if beta := request.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = beta
        async with httpx.AsyncClient(timeout=120.0) as client:
            upstream = await client.request(
                request.method,
                f"https://api.anthropic.com/{path}",
                content=await request.body(),
                headers=headers,
                params=request.query_params,
            )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    @app.post("/mcp")
    async def mcp(request: Request):
        binding = bindings.resolve(_run_token(request), "source.read")
        message = await request.json()
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "capability-broker", "version": "1.0"}}}
        if method == "notifications/initialized":
            return Response(status_code=202)
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": [
                    {"name": "get_pr_context", "description": "Read the bound pull request.", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "list_review_comments", "description": "Read review comments for the bound pull request.", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "list_check_results", "description": "Read checks for the bound PR head.", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "request_push_changes", "description": "Request approved publication of local edits.", "inputSchema": {"type": "object", "properties": {"summary": {"type": "string"}, "commit_message": {"type": "string"}}, "required": ["summary", "commit_message"]}},
                ]},
            }
        if method != "tools/call":
            raise HTTPException(status_code=404, detail="unsupported MCP method")
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        owner, repo = binding.repository.split("/", 1)
        if name == "get_pr_context":
            result = await _github_get(github_token, f"/repos/{owner}/{repo}/pulls/{binding.pr_number}")
        elif name == "list_review_comments":
            result = await _github_get(github_token, f"/repos/{owner}/{repo}/pulls/{binding.pr_number}/comments")
        elif name == "list_check_results":
            pr = await _github_get(github_token, f"/repos/{owner}/{repo}/pulls/{binding.pr_number}")
            result = await _github_get(github_token, f"/repos/{owner}/{repo}/commits/{pr['head']['sha']}/check-runs")
        elif name == "request_push_changes":
            bindings.resolve(_run_token(request), "changes.publish")
            result = await _request_push_update(binding.workflow_id, OperationRequest.model_validate(args))
            result = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        else:
            raise HTTPException(status_code=404, detail="tool not exposed")
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": __import__("json").dumps(result)}]}}

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


def build_default_app() -> FastAPI:
    return create_proxy_app(
        github_token=os.environ["GITHUB_TOKEN"],
        anthropic_key=os.environ["ANTHROPIC_API_KEY"],
        registration_secret=os.environ.get("BROKER_REGISTRATION_SECRET", ""),
    )


app = build_default_app() if os.environ.get("CAPABILITY_BROKER_BOOT") == "1" else None
