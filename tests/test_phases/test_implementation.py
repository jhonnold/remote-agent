# tests/test_phases/test_implementation.py
import pytest
from unittest.mock import AsyncMock
from remote_agent.phases.implementation import ImplementationHandler
from remote_agent.models import Issue, Event
from remote_agent.agent import AgentResult


@pytest.fixture
def deps():
    return {"db": AsyncMock(), "github": AsyncMock(), "agent_service": AsyncMock(), "workspace_mgr": AsyncMock()}


@pytest.fixture
def handler(deps):
    return ImplementationHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])


@pytest.fixture
def impl_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="", phase="implementing",
                 pr_number=10, branch_name="agent/issue-42",
                 workspace_path="/tmp/ws")


async def test_implementation_publishes_pr(handler, deps, impl_issue):
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s", cost_usd=2.0, input_tokens=500, output_tokens=1000,
    )
    # Mock reading the plan file
    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "## Plan content")
        result = await handler.handle(impl_issue, event)

    assert result.next_phase == "code_review"
    deps["github"].mark_pr_ready.assert_called_once()
