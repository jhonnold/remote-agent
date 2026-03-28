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
    gh = AsyncMock()
    gh.get_pr_comments.return_value = []
    gh.get_pr_reviews.return_value = []
    gh.get_pr_review_comments.return_value = []
    return gh


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
    # Create issue in code_review phase
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "code_review")
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
    await db.update_issue_phase(issue_id, "code_review")
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


async def test_poll_detects_new_pr_reviews(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "code_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 500, "body": "Needs changes", "state": "CHANGES_REQUESTED",
         "author": "testuser", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = [
        {"id": 900, "body": "use X here", "path": "src/foo.js", "line": 42,
         "author": "testuser", "review_id": 500, "created_at": "2026-01-01"}
    ]

    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1
    body = comment_events[0].payload["body"]
    assert "Needs changes" in body
    assert "src/foo.js:42" in body
    assert "use X here" in body


async def test_poll_review_with_only_inline_comments(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "code_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 600, "body": "", "state": "COMMENTED",
         "author": "testuser", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = [
        {"id": 901, "body": "fix this", "path": "src/bar.js", "line": 10,
         "author": "testuser", "review_id": 600, "created_at": "2026-01-01"}
    ]

    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1
    body = comment_events[0].payload["body"]
    assert "src/bar.js:10" in body
    assert "fix this" in body


async def test_poll_filters_dismissed_reviews(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "code_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 700, "body": "old review", "state": "DISMISSED",
         "author": "testuser", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = []

    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 0


async def test_poll_filters_non_allowlisted_review_author(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "code_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 800, "body": "looks good", "state": "APPROVED",
         "author": "stranger", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = []

    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 0


async def test_poll_updates_last_review_id(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "code_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 500, "body": "ok", "state": "COMMENTED",
         "author": "testuser", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = []

    await poller.poll_once()
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.last_review_id == 500

    # Second poll should not re-create event
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1  # Still just 1


async def test_poll_skips_completed_issue_not_closed(poller, db, mock_github):
    """Completed issue still open on GitHub should NOT create reopen event (the PR #131 bug)."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    # issue_closed_seen defaults to False

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 0  # No reopen event!


async def test_poll_detects_issue_closure(poller, db, mock_github):
    """When a completed issue disappears from open list, mark it as closed."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")

    mock_github.list_issues.return_value = []  # Issue is no longer open
    mock_github.get_pr_comments.return_value = [
        {"id": 50, "body": "old comment", "author": "testuser", "created_at": "2026-01-01"},
    ]
    await poller.poll_once()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.issue_closed_seen is True
    assert issue.last_issue_comment_id == 50


async def test_poll_creates_reopen_event_after_close_and_new_comment(poller, db, mock_github):
    """Genuine reopen: closed issue reappears with new comment."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.mark_issue_closed(issue_id, last_issue_comment_id=50)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 50, "body": "old comment", "author": "testuser", "created_at": "2026-01-01"},
        {"id": 100, "body": "Please reopen this", "author": "testuser", "created_at": "2026-01-02"},
    ]
    await poller.poll_once()

    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0].event_type == "reopen"


async def test_poll_no_reopen_without_new_comment(poller, db, mock_github):
    """Closed issue reopened but no new comment — no reopen event."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.mark_issue_closed(issue_id, last_issue_comment_id=50)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 50, "body": "old comment", "author": "testuser", "created_at": "2026-01-01"},
    ]
    await poller.poll_once()

    events = await db.get_unprocessed_events()
    assert len(events) == 0


async def test_poll_reopen_filters_non_allowlisted_comments(poller, db, mock_github):
    """New comment from non-allowlisted user should not trigger reopen."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.mark_issue_closed(issue_id, last_issue_comment_id=50)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "random comment", "author": "stranger", "created_at": "2026-01-02"},
    ]
    await poller.poll_once()

    events = await db.get_unprocessed_events()
    assert len(events) == 0


async def test_polls_issue_comments_for_design_review(poller, db, mock_github):
    """Design review issues poll issue comments (not PR comments) since no PR exists yet."""
    issue_id = await db.create_issue("owner", "repo", {"number": 42, "title": "Add feature", "body": "Details"})
    await db.update_issue_phase(issue_id, "design_review")

    mock_github.list_issues.return_value = [
        {"number": 42, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "Looks good", "author": "testuser", "created_at": "2026-01-01"}
    ]

    await poller.poll_once()

    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1

    # Verify last_issue_comment_id was updated (not last_comment_id)
    issue = await db.get_issue("owner", "repo", 42)
    assert issue.last_issue_comment_id == 100


async def test_design_review_filters_non_allowlisted_comments(poller, db, mock_github):
    """Design review should filter out comments from non-allowlisted users."""
    issue_id = await db.create_issue("owner", "repo", {"number": 42, "title": "Add feature", "body": "Details"})
    await db.update_issue_phase(issue_id, "design_review")

    mock_github.list_issues.return_value = [
        {"number": 42, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "Random comment", "author": "stranger", "created_at": "2026-01-01"}
    ]

    await poller.poll_once()

    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 0


async def test_design_review_skips_already_seen_comments(poller, db, mock_github):
    """Design review should not re-create events for already-seen comments."""
    issue_id = await db.create_issue("owner", "repo", {"number": 42, "title": "Add feature", "body": "Details"})
    await db.update_issue_phase(issue_id, "design_review")
    await db.update_last_issue_comment_id(issue_id, 100)

    mock_github.list_issues.return_value = [
        {"number": 42, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "Old comment", "author": "testuser", "created_at": "2026-01-01"}
    ]

    await poller.poll_once()

    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 0


async def test_poll_skips_error_issue_not_closed(poller, db, mock_github):
    """Error issue still open on GitHub should NOT create reopen event."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "error")

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 0
