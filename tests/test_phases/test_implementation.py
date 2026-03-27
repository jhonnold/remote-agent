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


async def test_implementation_publishes_comment_to_existing_pr(handler, deps, impl_issue):
    """When PR already exists, publish update comment to the existing PR."""
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"

    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "## Plan")
        result = await handler.handle(impl_issue, event)

    assert result.next_phase == "code_review"
    # Verify it uses the existing PR
    deps["github"].create_pr.assert_not_called()
    deps["db"].update_issue_pr.assert_not_called()
    # Verify comment posted to existing PR
    deps["github"].post_comment.assert_called_once()
    assert deps["github"].post_comment.call_args[0][2] == impl_issue.pr_number
    # Never calls mark_pr_ready
    deps["github"].mark_pr_ready.assert_not_called()


async def test_implementation_creates_pr_when_none_exists(handler, deps):
    """First implementation creates a non-draft PR."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="implementing",
                  branch_name="agent/issue-42", pr_number=None)
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["github"].create_pr.return_value = 15

    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "## Plan")
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    deps["github"].create_pr.assert_called_once()
    # Verify not draft
    call_kwargs = deps["github"].create_pr.call_args
    draft_value = call_kwargs.kwargs.get("draft", False)
    assert draft_value is not True
    deps["db"].update_issue_pr.assert_called_once_with(1, 15)
    # mark_pr_ready should NOT be called when creating a new PR
    deps["github"].mark_pr_ready.assert_not_called()


async def test_implementation_reuses_existing_pr(handler, deps, impl_issue):
    """Revision cycle with existing PR should not create a new one."""
    event = Event(id=1, issue_id=1, event_type="revision_requested",
                  payload={"body": "Fix the tests"})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"

    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "## Plan")
        result = await handler.handle(impl_issue, event)

    assert result.next_phase == "code_review"
    deps["github"].create_pr.assert_not_called()
    deps["db"].update_issue_pr.assert_not_called()


async def test_implementation_returns_error_when_plan_missing(handler, deps):
    """Plan file not found should return error result."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="implementing",
                  branch_name="agent/issue-42", pr_number=10)
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"

    # Plan file doesn't exist (no MonkeyPatch to make it exist)
    result = await handler.handle(issue, event)

    assert result.next_phase == "error"
    assert result.error_message is not None
    assert "plan" in result.error_message.lower()
    # Should not post any comment or create PR
    deps["github"].post_comment.assert_not_called()
    deps["github"].create_pr.assert_not_called()


async def test_implementation_audit_records(deps):
    audit = AsyncMock()
    handler = ImplementationHandler(deps["db"], deps["github"], deps["agent_service"],
                                     deps["workspace_mgr"], audit=audit)

    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="implementing",
                  pr_number=10, branch_name="agent/issue-42")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "## Plan")
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    assert audit.log.call_count >= 1
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "phase_transition" in categories
