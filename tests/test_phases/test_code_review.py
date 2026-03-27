# tests/test_phases/test_code_review.py
import pytest
from unittest.mock import AsyncMock
from remote_agent.phases.code_review import CodeReviewHandler
from remote_agent.models import Issue, Event
from remote_agent.agent import CommentInterpretation


@pytest.fixture
def deps():
    return {"db": AsyncMock(), "github": AsyncMock(), "agent_service": AsyncMock(), "workspace_mgr": AsyncMock()}


@pytest.fixture
def handler(deps):
    return CodeReviewHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])


@pytest.fixture
def review_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="", phase="code_review",
                 pr_number=10, branch_name="agent/issue-42",
                 plan_commit_hash="abc123", workspace_path="/tmp/ws")


async def test_approve_completes(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "completed"
    deps["workspace_mgr"].cleanup.assert_called_once()


async def test_revise_creates_event(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Fix errors"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="revise")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "implementing"
    deps["db"].create_event.assert_called_once()


async def test_back_to_planning_resets_state(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Rethink approach"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="back_to_planning")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "planning"
    deps["db"].set_plan_approved.assert_called_once_with(1, False)
    deps["github"].close_pr.assert_called_once()
    deps["github"].mark_pr_draft.assert_not_called()
    deps["db"].update_issue_pr.assert_called_once_with(1, None)
    deps["workspace_mgr"].reset_to_commit.assert_called_once()
    deps["db"].create_event.assert_called_once()



async def test_back_to_planning_without_workspace_path(handler, deps):
    """back_to_planning should skip reset_to_commit when workspace_path is None."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42",
                  plan_commit_hash="abc123", workspace_path=None)  # workspace_path is None
    event = Event(id=1, issue_id=1, event_type="new_comment",
                  payload={"body": "back to planning"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(
        intent="back_to_planning")

    result = await handler.handle(issue, event)

    assert result.next_phase == "planning"
    # Should close PR and clear pr_number
    deps["github"].close_pr.assert_called_once()
    deps["db"].update_issue_pr.assert_called_once_with(1, None)
    # Should NOT call reset_to_commit because workspace_path is None
    deps["workspace_mgr"].reset_to_commit.assert_not_called()
    # But create_event should still be called
    deps["db"].create_event.assert_called_once()


async def test_code_review_approve_audit(deps):
    audit = AsyncMock()
    handler = CodeReviewHandler(deps["db"], deps["github"], deps["agent_service"],
                                 deps["workspace_mgr"], audit=audit)

    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42")
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Ship it"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    result = await handler.handle(issue, event)

    assert result.next_phase == "completed"
    assert audit.log.call_count >= 1
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "comment_classification" in categories
    assert "phase_transition" in categories
