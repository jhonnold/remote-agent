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


def test_get_planning_subagents(agent_service):
    agents = agent_service._get_planning_subagents()
    assert "codebase-explorer" in agents


def test_get_implementation_subagents(agent_service):
    agents = agent_service._get_implementation_subagents()
    assert "implementer" in agents
    assert "spec-reviewer" in agents
    assert "code-reviewer" in agents


def test_classify_lgtm_approves(agent_service):
    interp = agent_service._classify_comment_text("LGTM", "plan_review")
    assert interp.intent == "approve"


def test_classify_plan_approved(agent_service):
    interp = agent_service._classify_comment_text(
        "[Review \u2014 COMMENTED]\n\nPlan approved.", "plan_review"
    )
    assert interp.intent == "approve"


def test_classify_changes_requested_revises(agent_service):
    interp = agent_service._classify_comment_text(
        "[Review \u2014 CHANGES_REQUESTED]\n\nPlease fix the tests.", "plan_review"
    )
    assert interp.intent == "revise"


def test_classify_github_approved_review(agent_service):
    interp = agent_service._classify_comment_text(
        "[Review \u2014 APPROVED]\n\nShip it!", "code_review"
    )
    assert interp.intent == "approve"


def test_classify_question(agent_service):
    interp = agent_service._classify_comment_text(
        "Why did you choose this approach?", "plan_review"
    )
    assert interp.intent == "question"


def test_classify_back_to_planning(agent_service):
    interp = agent_service._classify_comment_text(
        "Let's rethink this approach", "code_review"
    )
    assert interp.intent == "back_to_planning"


def test_classify_unknown_defaults_to_revise(agent_service):
    interp = agent_service._classify_comment_text(
        "Change the database schema to use UUIDs", "plan_review"
    )
    assert interp.intent == "revise"


def test_classify_inline_comments_not_approve(agent_service):
    interp = agent_service._classify_comment_text(
        "[Review \u2014 COMMENTED]\n\nLooks good\n\nInline comments:\n- src/foo.py:10 \u2014 fix this",
        "plan_review",
    )
    assert interp.intent == "revise"
