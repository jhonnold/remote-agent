# tests/test_phases/test_design_review.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock
from remote_agent.phases.design_review import DesignReviewHandler
from remote_agent.models import Issue, Event
from remote_agent.agent import CommentInterpretation


@pytest.fixture
def deps():
    return {"db": AsyncMock(), "github": AsyncMock(), "agent_service": AsyncMock()}


@pytest.fixture
def handler(deps):
    return DesignReviewHandler(deps["db"], deps["github"], deps["agent_service"])


@pytest.fixture
def review_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="Need OAuth2", phase="design_review")


async def test_approve_transitions_to_planning(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "planning"
    deps["db"].set_design_approved.assert_called_once_with(1, True)
    # Confirmation posted on the issue (issue_number=42), not a PR
    deps["github"].post_comment.assert_called_once()
    call_args = deps["github"].post_comment.call_args
    assert call_args[0][0] == "o"
    assert call_args[0][1] == "r"
    assert call_args[0][2] == 42
    assert "Design approved" in call_args[0][3]
    # Event created to drive planning
    deps["db"].create_event.assert_called_once_with(1, "revision_requested", {})


async def test_revise_transitions_to_designing(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment",
                  payload={"body": "Change approach to use JWT"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="revise")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "designing"
    deps["db"].create_event.assert_called_once_with(1, "revision_requested", event.payload)


async def test_question_posts_answer_and_stays(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment",
                  payload={"body": "Why did you choose this approach?"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="question")
    deps["agent_service"].answer_question.return_value = "Because it scales better."
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "design_review"
    # answer_question called with correct args
    deps["agent_service"].answer_question.assert_called_once_with(
        question="Why did you choose this approach?",
        context="design_review",
        issue_title="Add auth",
        issue_body="Need OAuth2",
        issue_id=1,
    )
    # Answer posted on the issue
    deps["github"].post_comment.assert_called_once()
    call_args = deps["github"].post_comment.call_args
    assert call_args[0][0] == "o"
    assert call_args[0][1] == "r"
    assert call_args[0][2] == 42
    assert call_args[0][3] == "Because it scales better."


async def test_unknown_intent_stays_in_design_review(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "hmm"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="unknown")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "design_review"


async def test_approve_audit(deps):
    audit = AsyncMock()
    handler = DesignReviewHandler(deps["db"], deps["github"], deps["agent_service"], audit=audit)

    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="design_review")
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    result = await handler.handle(issue, event)

    assert result.next_phase == "planning"
    assert audit.log.call_count >= 2
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "comment_classification" in categories
    assert "phase_transition" in categories
