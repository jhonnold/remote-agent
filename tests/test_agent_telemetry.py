from __future__ import annotations
from unittest.mock import patch, AsyncMock, MagicMock
from dataclasses import dataclass, field
from remote_agent.agent import AgentService


@dataclass
class FakeResultMessage:
    subtype: str = "result"
    duration_ms: int = 5000
    duration_api_ms: int = 4000
    is_error: bool = False
    num_turns: int = 3
    session_id: str = "test-session"
    total_cost_usd: float = 0.05
    usage: dict = field(default_factory=lambda: {"input_tokens": 1500, "output_tokens": 800})
    result: str = "done"
    model_usage: dict = field(default_factory=lambda: {
        "claude-sonnet-4-6": {
            "inputTokens": 1500,
            "outputTokens": 800,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0,
            "costUSD": 0.05,
        }
    })


async def test_run_query_calls_record_query_metrics():
    """_run_query should call record_query_metrics with model_usage from ResultMessage."""
    mock_db = MagicMock()
    mock_db.create_agent_run = AsyncMock(return_value=1)
    mock_db.get_latest_session_for_phase = AsyncMock(return_value=None)
    mock_db.complete_agent_run = AsyncMock()

    mock_config = MagicMock()
    mock_config.repos = []
    service = AgentService(mock_config, mock_db)

    msg = FakeResultMessage()

    async def fake_query(**kwargs):
        yield msg

    mock_options = MagicMock()
    mock_options.model = "sonnet"

    with (
        patch("claude_agent_sdk.query", side_effect=fake_query),
        patch("claude_agent_sdk.ResultMessage", FakeResultMessage),
        patch("remote_agent.agent.record_query_metrics") as mock_record,
    ):
        await service._run_query("test prompt", mock_options, issue_id=1, phase="designing")

        mock_record.assert_called_once_with(
            repo="",
            phase="designing",
            model_usage=msg.model_usage,
            duration_ms=5000,
            duration_api_ms=4000,
        )


async def test_run_query_calls_record_query_error_on_failure():
    """_run_query should call record_query_error when query raises."""
    mock_db = MagicMock()
    mock_db.create_agent_run = AsyncMock(return_value=1)
    mock_db.get_latest_session_for_phase = AsyncMock(return_value=None)
    mock_db.complete_agent_run = AsyncMock()

    mock_config = MagicMock()
    mock_config.repos = []
    service = AgentService(mock_config, mock_db)

    async def failing_query(**kwargs):
        raise RuntimeError("API error")
        yield  # make it an async generator

    mock_options = MagicMock()
    mock_options.model = "sonnet"

    with (
        patch("claude_agent_sdk.query", side_effect=failing_query),
        patch("claude_agent_sdk.ResultMessage"),
        patch("remote_agent.agent.record_query_error") as mock_error,
    ):
        import pytest
        from remote_agent.exceptions import AgentError
        with pytest.raises(AgentError):
            await service._run_query("test prompt", mock_options, issue_id=1, phase="designing")

        mock_error.assert_called_once_with(
            repo="", phase="designing", model="sonnet",
        )


async def test_repo_label_from_config():
    """_repo_label should build owner/name from first configured repo."""
    mock_config = MagicMock()
    repo = MagicMock()
    repo.owner = "myorg"
    repo.name = "myrepo"
    mock_config.repos = [repo]

    service = AgentService(mock_config, MagicMock())
    assert service._repo_label() == "myorg/myrepo"


async def test_repo_label_empty_when_no_repos():
    """_repo_label should return empty string when no repos configured."""
    mock_config = MagicMock()
    mock_config.repos = []

    service = AgentService(mock_config, MagicMock())
    assert service._repo_label() == ""
