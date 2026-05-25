"""SDK-MCP wrapper around _local_repo_impl pure functions.

Each tool is registered with the Claude Agent SDK via `tool(...)` and grouped
into an in-process MCP server via `create_sdk_mcp_server`. Tools resolve the
workdir from the `AUTOFIX_WORKDIR_ID` env var (set by `run_agent_iteration`
before calling `query()`) — there is no `RunContext` equivalent in SDK MCP.

We use a **split pattern**:

  - `_<name>_impl(args: dict) -> dict` is the bare async coroutine doing the
    work. These are what the tests `await` directly.
  - `<name>_tool = tool(name, desc, schema)(_<name>_impl)` produces the
    `SdkMcpTool` dataclass the SDK introspects.

This is necessary because `claude_agent_sdk.tool(...)` is *not* a pass-through
decorator — it returns an `SdkMcpTool` instance which is not itself awaitable.
"""
import json

from claude_agent_sdk import tool, create_sdk_mcp_server

from src.tools import _local_repo_impl as impl
from src.tools._workdir import (
    workdir_root_from_env,
    get_sandbox_handle,
)


def _text(payload: str | dict) -> dict:
    """Return the SDK content shape from a string or JSON-able dict."""
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload)
    return {"content": [{"type": "text", "text": body}]}


def _exec_target():
    """Resolve the dispatch target for the 4 command-execution tools.

    Returns the per-workflow SandboxHandle when one is bound to the
    asyncio task; otherwise falls back to the host workdir path
    (legacy behavior, kept for tests).
    """
    handle = get_sandbox_handle()
    return handle if handle is not None else workdir_root_from_env()


# ---------- read_file ----------

async def _read_file_impl(args: dict) -> dict:
    return _text(impl.read_file(workdir_root_from_env(), args["path"]))


read_file_tool = tool(
    "read_file",
    "Read a file inside the PR working copy.",
    {"path": str},
)(_read_file_impl)


# ---------- list_files ----------

async def _list_files_impl(args: dict) -> dict:
    glob = args.get("glob", "**/*.py")
    return _text(json.dumps(impl.list_files(workdir_root_from_env(), glob)))


list_files_tool = tool(
    "list_files",
    "List files in the working copy matching a glob (default '**/*.py').",
    {"glob": str},
)(_list_files_impl)


# ---------- apply_edit ----------

async def _apply_edit_impl(args: dict) -> dict:
    sha = impl.apply_edit(
        workdir_root_from_env(), args["path"], args["new_content"]
    )
    return _text(sha)


apply_edit_tool = tool(
    "apply_edit",
    "Overwrite a file with new content. Returns the SHA-1 of the new content.",
    {"path": str, "new_content": str},
)(_apply_edit_impl)


# ---------- run_ruff ----------

async def _run_ruff_impl(args: dict) -> dict:
    result = impl.run_ruff(_exec_target())
    return _text(result.model_dump())


run_ruff_tool = tool(
    "run_ruff",
    "Run ruff check on the working copy. Returns a JSON RuffResult.",
    {},
)(_run_ruff_impl)


# ---------- run_pytest ----------

async def _run_pytest_impl(args: dict) -> dict:
    target = args.get("target") or None
    result = impl.run_pytest(_exec_target(), target)
    return _text(result.model_dump())


run_pytest_tool = tool(
    "run_pytest",
    "Run pytest in the working copy. Optional target (file::test). Returns a JSON PytestResult.",
    {"target": str},
)(_run_pytest_impl)


# ---------- git_status ----------

async def _git_status_impl(args: dict) -> dict:
    return _text(impl.git_status(_exec_target()).model_dump())


git_status_tool = tool(
    "git_status",
    "Return the git status of the working copy as a JSON GitStatus.",
    {},
)(_git_status_impl)


# ---------- git_commit_and_push ----------

async def _git_commit_and_push_impl(args: dict) -> dict:
    result = impl.git_commit_and_push(_exec_target(), args["message"])
    return _text(result.model_dump())


git_commit_and_push_tool = tool(
    "git_commit_and_push",
    "Stage all changes, commit with the given message, fetch, refuse if remote advanced, "
    "push. Returns a JSON CommitResult. The commit message is automatically tagged with "
    "the [autofix-bot] trailer.",
    {"message": str},
)(_git_commit_and_push_impl)


# ---------- server ----------

local_repo_mcp_server = create_sdk_mcp_server(
    name="repo",
    version="1.0.0",
    tools=[
        read_file_tool,
        list_files_tool,
        apply_edit_tool,
        run_ruff_tool,
        run_pytest_tool,
        git_status_tool,
        git_commit_and_push_tool,
    ],
)
