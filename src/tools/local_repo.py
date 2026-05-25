from pydantic_ai import FunctionToolset, RunContext

from src.models import AgentDeps
from src.tools._local_repo_impl import RuffResult, PytestResult, GitStatus, CommitResult
from src.tools._workdir import workdir_root
from src.tools import _local_repo_impl as impl


local_repo_toolset = FunctionToolset[AgentDeps](id="repo")


@local_repo_toolset.tool
def read_file(ctx: RunContext[AgentDeps], path: str) -> str:
    """Read a file in the working copy."""
    return impl.read_file(workdir_root(ctx.deps.workdir_id), path)


@local_repo_toolset.tool
def list_files(ctx: RunContext[AgentDeps], glob: str = "**/*.py") -> list[str]:
    """List files in the working copy matching glob."""
    return impl.list_files(workdir_root(ctx.deps.workdir_id), glob)


@local_repo_toolset.tool
def apply_edit(ctx: RunContext[AgentDeps], path: str, new_content: str) -> str:
    """Overwrite a file with new content. Returns SHA-1 of new content."""
    return impl.apply_edit(workdir_root(ctx.deps.workdir_id), path, new_content)


@local_repo_toolset.tool
def run_ruff(ctx: RunContext[AgentDeps]) -> RuffResult:
    """Run ruff check on the working copy. Returns violations as structured data."""
    return impl.run_ruff(workdir_root(ctx.deps.workdir_id))


@local_repo_toolset.tool
def run_pytest(ctx: RunContext[AgentDeps], target: str | None = None) -> PytestResult:
    """Run pytest. Optionally limit to a target (file::test)."""
    return impl.run_pytest(workdir_root(ctx.deps.workdir_id), target)


@local_repo_toolset.tool
def git_status(ctx: RunContext[AgentDeps]) -> GitStatus:
    """Return the git status of the working copy."""
    return impl.git_status(workdir_root(ctx.deps.workdir_id))


@local_repo_toolset.tool
def git_commit_and_push(ctx: RunContext[AgentDeps], message: str) -> CommitResult:
    """Stage all changes, commit, fetch, refuse if remote advanced, push."""
    return impl.git_commit_and_push(workdir_root(ctx.deps.workdir_id), message)
