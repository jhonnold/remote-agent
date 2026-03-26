# tests/test_poller.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from remote_agent.poller import Poller
from remote_agent.config import Config, RepoConfig, PollingConfig, TriggerConfig


@pytest.fixture
def mock_config():
    config = MagicMock(spec=Config)
    config.repos = [RepoConfig(owner="owner", name="repo")]
    config.users = ["testuser"]
    config.polling = PollingConfig(interval_seconds=60)
    config.trigger = TriggerConfig(label="agent")
    return config


@pytest.fixture
async def db(tmp_path):
    from remote_agent.db import Database
    database = await Database.initialize(str(tmp_path / "test.db"))
    yield database
    await database.close()


@pytest.fixture
def mock_github():
    return AsyncMock()


@pytest.fixture
def poller(mock_config, db, mock_github):
    return Poller(mock_config, db, mock_github)


async def test_poll_new_issue_creates_event(poller, db, mock_github):
    mock_github.list_issues.return_value = [
        {"number": 1, "title": "Test", "body": "Body", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0].event_type == "new_issue"


async def test_poll_ignores_non_allowlisted_user(poller, db, mock_github):
    mock_github.list_issues.return_value = [
        {"number": 1, "title": "Test", "body": "Body", "author": {"login": "stranger"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 0


async def test_poll_ignores_already_tracked_issue(poller, db, mock_github):
    mock_github.list_issues.return_value = [
        {"number": 1, "title": "Test", "body": "Body", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    # Second poll should not create duplicate
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 1


async def test_poll_detects_new_pr_comments(poller, db, mock_github):
    # Create issue in plan_review phase
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "LGTM", "author": "testuser", "created_at": "2026-01-01"}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1


async def test_poll_filters_agent_own_comments(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "Plan created.", "author": "bot-user", "created_at": "2026-01-01"}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 0
