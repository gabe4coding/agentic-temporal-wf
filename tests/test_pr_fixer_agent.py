from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from src.agents.pr_fixer import agent
from src.models import AgentDeps, PRRef, FixPlan


@pytest.fixture
def agent_deps(tmp_repo: Path) -> AgentDeps:
    """Point the workdir helper at a tmp repo by monkeypatching workdir_root."""
    return AgentDeps(
        workdir_id="test",
        pr=PRRef(owner="o", repo="r", number=1, head_sha="a", head_ref="main"),
    )


async def test_agent_returns_fix_plan_with_test_model(agent_deps: AgentDeps):
    """With TestModel, agent returns a synthesized FixPlan without real LLM calls."""
    test_model = TestModel(custom_output_args={
        "action": "no_action_needed",
        "summary": "stub",
    })
    with agent.override(model=test_model, toolsets=[]):
        result = await agent.run("hello", deps=agent_deps)
    assert isinstance(result.output, FixPlan)
    assert result.output.action == "no_action_needed"
