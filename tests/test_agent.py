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


def test_parse_interpretation_valid(agent_service):
    result_text = json.dumps({"intent": "approve", "response": "Looks good"})
    interp = agent_service._parse_interpretation(result_text)
    assert interp.intent == "approve"


def test_parse_interpretation_invalid_defaults_to_revise(agent_service):
    interp = agent_service._parse_interpretation("unparseable garbage")
    assert interp.intent == "revise"


def test_parse_interpretation_none_defaults_to_revise(agent_service):
    interp = agent_service._parse_interpretation(None)
    assert interp.intent == "revise"
