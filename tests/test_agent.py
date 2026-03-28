# tests/test_agent.py
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.agent import AgentService, CommentInterpretation
from remote_agent.config import AgentConfig


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.agent = AgentConfig()
    return config


@pytest.fixture
async def mock_db(tmp_path):
    from remote_agent.db import Database
    db = await Database.initialize(str(tmp_path / "test.db"))
    yield db
    await db.close()


@pytest.fixture
def agent_service(mock_config, mock_db):
    return AgentService(mock_config, mock_db)


def test_comment_interpretation_dataclass():
    interp = CommentInterpretation(intent="approve", response="Plan approved.")
    assert interp.intent == "approve"
    assert interp.response == "Plan approved."


def test_agent_service_has_run_designing_method(agent_service):
    assert hasattr(agent_service, 'run_designing')


def test_agent_service_has_answer_question_method(agent_service):
    assert hasattr(agent_service, 'answer_question')


def test_get_designing_subagents(agent_service):
    subagents = agent_service._get_designing_subagents("Test issue body")
    assert "codebase-explorer" in subagents
    assert "issue-advocate" in subagents
    assert "design-critic" in subagents


def test_get_planning_subagents_updated(agent_service):
    subagents = agent_service._get_planning_subagents()
    assert "codebase-explorer" in subagents
    assert "plan-reviewer" in subagents


def test_get_implementation_subagents_updated(agent_service):
    subagents = agent_service._get_implementation_subagents("Issue body text")
    assert "implementer" in subagents
    assert "spec-reviewer" in subagents
    assert "code-reviewer" in subagents
    assert "issue-advocate" in subagents
    assert "final-reviewer" in subagents


def test_classify_lgtm_approves(agent_service):
    interp = agent_service._classify_comment_text("LGTM", "design_review")
    assert interp.intent == "approve"


def test_classify_plan_approved(agent_service):
    interp = agent_service._classify_comment_text(
        "[Review \u2014 COMMENTED]\n\nPlan approved.", "design_review"
    )
    assert interp.intent == "approve"


def test_classify_changes_requested_revises(agent_service):
    interp = agent_service._classify_comment_text(
        "[Review \u2014 CHANGES_REQUESTED]\n\nPlease fix the tests.", "design_review"
    )
    assert interp.intent == "revise"


def test_classify_github_approved_review(agent_service):
    interp = agent_service._classify_comment_text(
        "[Review \u2014 APPROVED]\n\nShip it!", "code_review"
    )
    assert interp.intent == "approve"


def test_classify_question(agent_service):
    interp = agent_service._classify_comment_text(
        "Why did you choose this approach?", "design_review"
    )
    assert interp.intent == "question"


def test_classify_back_to_design(agent_service):
    result = agent_service._classify_comment_text("let's rethink the design", "code_review")
    assert result.intent == "back_to_design"


def test_classify_unknown_defaults_to_revise(agent_service):
    interp = agent_service._classify_comment_text(
        "Change the database schema to use UUIDs", "design_review"
    )
    assert interp.intent == "revise"


def test_classify_inline_comments_not_approve(agent_service):
    interp = agent_service._classify_comment_text(
        "[Review \u2014 COMMENTED]\n\nLooks good\n\nInline comments:\n- src/foo.py:10 \u2014 fix this",
        "design_review",
    )
    assert interp.intent == "revise"
