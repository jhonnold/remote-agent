# tests/test_phases/test_plan_review.py
import pytest
from unittest.mock import AsyncMock
from remote_agent.phases.plan_review import PlanReviewHandler
from remote_agent.models import Issue, Event
from remote_agent.agent import CommentInterpretation


@pytest.fixture
def deps():
    return {"db": AsyncMock(), "github": AsyncMock(), "agent_service": AsyncMock()}


@pytest.fixture
def handler(deps):
    return PlanReviewHandler(deps["db"], deps["github"], deps["agent_service"])


@pytest.fixture
def review_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="", phase="plan_review", pr_number=10)


async def test_approve_transitions_to_implementing(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "implementing"
    deps["db"].set_plan_approved.assert_called_once_with(1, True)
    # Must create event to drive implementation handler
    deps["db"].create_event.assert_called_once()


async def test_revise_creates_event_and_stays(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Change X"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="revise")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "planning"
    deps["db"].create_event.assert_called_once()


async def test_question_posts_response_and_stays(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Why X?"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(
        intent="question", response="Because Y.")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "plan_review"
    deps["github"].post_comment.assert_called_once()


async def test_plan_review_approve_audit(deps):
    audit = AsyncMock()
    handler = PlanReviewHandler(deps["db"], deps["github"], deps["agent_service"], audit=audit)

    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="plan_review", pr_number=10)
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    result = await handler.handle(issue, event)

    assert result.next_phase == "implementing"
    assert audit.log.call_count >= 1
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "comment_classification" in categories
