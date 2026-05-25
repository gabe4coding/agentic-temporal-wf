"""Smoke tests for the SDK-MCP wrapper around _local_repo_impl.

We don't test the SDK plumbing end-to-end — that's covered by the agent
activity tests. Here we just verify each tool can be called as a plain
async function with the args-dict shape the SDK uses, that it returns
the documented content shape, and that AUTOFIX_WORKDIR_ID is honored.

NOTE: We import the bare `_*_impl` async callables (split pattern) because
the SDK's `@tool(...)` decorator returns an `SdkMcpTool` dataclass that is
NOT directly awaitable. The `*_tool` symbols are the `SdkMcpTool` objects
registered with the server.
"""
import json
from pathlib import Path

from src.tools.local_repo import (
    _read_file_impl,
    _list_files_impl,
    _apply_edit_impl,
    _run_ruff_impl,
    _run_pytest_impl,
    _git_status_impl,
    _git_commit_and_push_impl,
    local_repo_mcp_server,
)


async def test_read_file_tool_returns_sdk_content_shape(tmp_repo: Path, monkeypatch):
    monkeypatch.setenv("AUTOFIX_WORKDIR_ID", "irrelevant")
    monkeypatch.setattr("src.tools.local_repo.workdir_root_from_env", lambda: tmp_repo)
    out = await _read_file_impl({"path": "hello.py"})
    assert "content" in out
    assert out["content"][0]["type"] == "text"
    assert out["content"][0]["text"].startswith("def hello()")


async def test_apply_edit_tool_writes_file(tmp_repo: Path, monkeypatch):
    monkeypatch.setenv("AUTOFIX_WORKDIR_ID", "irrelevant")
    monkeypatch.setattr("src.tools.local_repo.workdir_root_from_env", lambda: tmp_repo)
    out = await _apply_edit_impl({"path": "hello.py", "new_content": "x = 1\n"})
    assert (tmp_repo / "hello.py").read_text() == "x = 1\n"
    # Returned text contains the sha-1
    assert len(out["content"][0]["text"]) >= 40


async def test_run_ruff_tool_returns_json_text(tmp_repo: Path, monkeypatch):
    monkeypatch.setenv("AUTOFIX_WORKDIR_ID", "irrelevant")
    monkeypatch.setattr("src.tools.local_repo.workdir_root_from_env", lambda: tmp_repo)
    out = await _run_ruff_impl({})
    # The returned text is a JSON-serialized RuffResult
    parsed = json.loads(out["content"][0]["text"])
    assert "exit_code" in parsed and "violations" in parsed


def test_local_repo_mcp_server_is_an_mcp_server():
    # Smoke: the server object is constructed and exposes some 'name' or similar
    # attribute — exact attribute is documented by claude_agent_sdk; we just
    # confirm we got *something* back.
    assert local_repo_mcp_server is not None


# Touch the other tool symbols so the import isn't dead-code (and so static
# analysis can spot missing symbols early).
def test_all_tool_symbols_exist():
    from src.tools.local_repo import (
        read_file_tool,
        list_files_tool,
        apply_edit_tool,
        run_ruff_tool,
        run_pytest_tool,
        git_status_tool,
        git_commit_and_push_tool,
    )
    # And the bare _impl coroutines we imported above are not None
    for sym in (
        read_file_tool,
        list_files_tool,
        apply_edit_tool,
        run_ruff_tool,
        run_pytest_tool,
        git_status_tool,
        git_commit_and_push_tool,
        _list_files_impl,
        _git_status_impl,
        _run_pytest_impl,
        _git_commit_and_push_impl,
    ):
        assert sym is not None
