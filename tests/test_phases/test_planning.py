# tests/test_phases/test_planning.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from remote_agent.phases.planning import PlanningHandler
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.agent import AgentResult


@pytest.fixture
def deps():
    return {
        "db": AsyncMock(),
        "github": AsyncMock(),
        "agent_service": AsyncMock(),
        "workspace_mgr": AsyncMock(),
    }


@pytest.fixture
def handler(deps):
    return PlanningHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])


@pytest.fixture
def new_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="Need OAuth2", phase="new")


@pytest.fixture
def new_issue_event():
    return Event(id=1, issue_id=1, event_type="new_issue",
                 payload={"number": 42, "title": "Add auth", "body": "Need OAuth2"})


async def test_planning_posts_plan_to_issue_not_pr(handler, deps, new_issue, new_issue_event):
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "# Plan\nSome plan content")
        result = await handler.handle(new_issue, new_issue_event)

    assert result.next_phase == "plan_review"
    # Should NOT create a draft PR
    deps["github"].create_pr.assert_not_called()
    deps["db"].update_issue_pr.assert_not_called()
    # Should post plan as issue comment using issue_number (42)
    deps["github"].post_comment.assert_called_once()
    call_args = deps["github"].post_comment.call_args
    assert call_args[0][2] == 42  # issue_number, not pr_number
    assert "# Plan" in call_args[0][3]  # Plan content in comment body


async def test_planning_revision_reuses_existing_pr(handler, deps, new_issue_event):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="planning",
                  pr_number=10, branch_name="agent/issue-42")
    event = Event(id=2, issue_id=1, event_type="revision_requested",
                  payload={"body": "Change approach"})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "def456"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-2", cost_usd=0.5, input_tokens=50, output_tokens=100,
    )

    result = await handler.handle(issue, event)

    assert result.next_phase == "plan_review"
    deps["github"].create_pr.assert_not_called()  # Planning no longer creates PRs
    # Verify plan was posted to the issue as a comment
    # In a revision scenario, the plan file may or may not exist on disk (workspace is mocked),
    # so the comment may contain actual plan content or the fallback message.
    deps["github"].post_comment.assert_called_once()
    call_args = deps["github"].post_comment.call_args
    assert call_args[0][2] == 42  # issue_number
    # Comment should contain either actual plan or fallback message
    assert "Plan" in call_args[0][3] or "Plan file created" in call_args[0][3]


async def test_planning_after_reopen_uses_force_branch(handler, deps):
    """After reopen, branch_name is None — ensure_branch should use force=True."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="planning",
                  branch_name=None)  # Cleared by reopen
    event = Event(id=3, issue_id=1, event_type="new_issue",
                  payload={"number": 42, "title": "Add auth", "body": "Need OAuth2"})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    result = await handler.handle(issue, event)

    assert result.next_phase == "plan_review"
    deps["workspace_mgr"].ensure_branch.assert_called_once_with("/tmp/ws", "agent/issue-42", force=True)


async def test_planning_audit_records(deps, new_issue, new_issue_event):
    audit = AsyncMock()
    handler = PlanningHandler(deps["db"], deps["github"], deps["agent_service"],
                               deps["workspace_mgr"], audit=audit)

    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    result = await handler.handle(new_issue, new_issue_event)

    assert result.next_phase == "plan_review"
    # Verify audit was called for phase transition (no longer logs PR creation)
    assert audit.log.call_count >= 1
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "phase_transition" in categories
