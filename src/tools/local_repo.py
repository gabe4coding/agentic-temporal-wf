from pydantic_ai import FunctionToolset, RunContext

from src.models import AgentDeps
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
