# tests/test_integration.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.db import Database
from remote_agent.poller import Poller
from remote_agent.dispatcher import Dispatcher
from remote_agent.config import Config, RepoConfig, PollingConfig, TriggerConfig, WorkspaceConfig, DatabaseConfig, AgentConfig, LoggingConfig
from remote_agent.agent import AgentResult, CommentInterpretation


@pytest.fixture
def config():
    return Config(
        repos=[RepoConfig(owner="owner", name="repo")],
        users=["testuser"],
        polling=PollingConfig(interval_seconds=60),
        trigger=TriggerConfig(label="agent"),
        workspace=WorkspaceConfig(base_dir="/tmp/ws"),
        database=DatabaseConfig(path=""),  # Will be overridden
        agent=AgentConfig(),
        logging=LoggingConfig(),
    )


@pytest.fixture
async def db(tmp_path):
    database = await Database.initialize(str(tmp_path / "test.db"))
    yield database
    await database.close()


@pytest.fixture
def github():
    return AsyncMock()


@pytest.fixture
def agent_service():
    return AsyncMock()


@pytest.fixture
def workspace_mgr():
    mgr = AsyncMock()
    mgr.cleanup = MagicMock()
    return mgr


async def test_full_lifecycle_happy_path(config, db, github, agent_service, workspace_mgr):
    """Test: new issue -> planning -> plan_review -> approve -> implementing -> code_review -> approve -> completed"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr)
    # Override dispatcher handlers to use our mocks
    dispatcher._planning.agent_service = agent_service
    dispatcher._planning.workspace_mgr = workspace_mgr
    dispatcher._planning.github = github
    dispatcher._plan_review.agent_service = agent_service
    dispatcher._plan_review.github = github
    dispatcher._implementation.agent_service = agent_service
    dispatcher._implementation.workspace_mgr = workspace_mgr
    dispatcher._implementation.github = github
    dispatcher._code_review.agent_service = agent_service
    dispatcher._code_review.workspace_mgr = workspace_mgr
    dispatcher._code_review.github = github

    # Step 1: Poller detects new issue
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "new"

    # Step 2: Dispatcher routes to planning
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "abc123"
    agent_service.run_planning.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )
    github.create_pr.return_value = 5

    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "plan_review"
    assert issue.pr_number == 5

    # Step 3: Human approves plan
    github.get_pr_comments.return_value = [
        {"id": 100, "body": "LGTM, implement it", "author": "testuser", "created_at": "2026-01-01"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "implementing"
    assert issue.plan_approved is True

    # Step 4: Implementation runs (triggered by revision_requested event from plan_review handler)
    agent_service.run_implementation.return_value = AgentResult(
        success=True, session_id="s2", cost_usd=3.0, input_tokens=500, output_tokens=1000,
    )

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="## Plan"):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "code_review"

    # Step 5: Human approves code
    github.get_pr_comments.return_value = [
        {"id": 200, "body": "Ship it!", "author": "testuser", "created_at": "2026-01-02"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "completed"
