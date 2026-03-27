# tests/test_integration.py
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.db import Database
from remote_agent.poller import Poller
from remote_agent.dispatcher import Dispatcher
from remote_agent.config import Config, RepoConfig, PollingConfig, TriggerConfig, WorkspaceConfig, DatabaseConfig, AgentConfig, LoggingConfig
from remote_agent.agent import AgentResult, CommentInterpretation
from remote_agent.audit import AuditLogger


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
    gh = AsyncMock()
    gh.get_pr_reviews.return_value = []
    gh.get_pr_review_comments.return_value = []
    return gh


@pytest.fixture
def agent_service():
    return AsyncMock()


@pytest.fixture
def workspace_mgr():
    mgr = AsyncMock()
    mgr.cleanup = MagicMock()
    return mgr


@pytest.fixture
async def audit(db, tmp_path):
    a = AuditLogger(db, str(tmp_path / "audit.jsonl"))
    yield a
    a.close()


@pytest.fixture
def audit_file(tmp_path):
    return tmp_path / "audit.jsonl"


async def test_full_lifecycle_happy_path(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    """Test: new issue -> planning -> plan_review -> approve -> implementing -> code_review -> approve -> completed"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
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

    # Step 2: Dispatcher routes to planning (no PR created yet)
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "abc123"
    agent_service.run_planning.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "plan_review"
    assert issue.pr_number is None  # No PR created during planning

    # Step 3: Human approves plan (polls issue comments — no PR yet)
    github.get_pr_comments.return_value = [
        {"id": 100, "body": "LGTM, implement it", "author": "testuser", "created_at": "2026-01-01"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "implementing"
    assert issue.plan_approved is True

    # Step 4: Implementation runs and creates the PR
    github.create_pr.return_value = 5
    agent_service.run_implementation.return_value = AgentResult(
        success=True, session_id="s2", cost_usd=3.0, input_tokens=500, output_tokens=1000,
    )

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="## Plan"):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "code_review"
    assert issue.pr_number == 5  # PR created during implementation

    # Step 5: Human approves code
    github.get_pr_comments.return_value = [
        {"id": 200, "body": "Ship it!", "author": "testuser", "created_at": "2026-01-02"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "completed"

    # Verify audit trail
    audit_lines = audit_file.read_text().strip().split("\n")
    audit_records = [json.loads(line) for line in audit_lines]
    categories_and_actions = [(r["category"], r["action"]) for r in audit_records]

    # Should have phase transition records for the full lifecycle (spec requires all 5)
    assert ("phase_transition", "plan_review") in categories_and_actions
    assert ("phase_transition", "implementing") in categories_and_actions
    assert ("phase_transition", "code_review") in categories_and_actions
    assert ("phase_transition", "completed") in categories_and_actions

    # Should also have comment classification records
    assert ("comment_classification", "approve") in categories_and_actions

    # All records should be successful
    assert all(r["success"] for r in audit_records)


async def test_review_comment_triggers_revision(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    """Test: plan_review -> user posts issue comment requesting revision -> back to planning"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
    dispatcher._planning.agent_service = agent_service
    dispatcher._planning.workspace_mgr = workspace_mgr
    dispatcher._planning.github = github
    dispatcher._plan_review.agent_service = agent_service
    dispatcher._plan_review.github = github

    # Setup: plan is posted to issue (no PR created during planning)
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "abc123"
    agent_service.run_planning.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    await poller.poll_once()
    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "plan_review"
    assert issue.pr_number is None  # No PR created during planning

    # User posts an issue comment requesting revision (no PR exists, so poller reads issue comments)
    github.get_pr_comments.return_value = [
        {"id": 100, "body": "Please revise the approach — use a different strategy",
         "author": "testuser", "created_at": "2026-01-02"}
    ]

    await poller.poll_once()

    # Verify event was created
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1
    payload = comment_events[0].payload
    assert "revise" in payload["body"].lower()

    # Dispatcher routes to plan_review handler, which classifies as revise
    agent_service.interpret_comment.return_value = CommentInterpretation(intent="revise")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "planning"  # Revision sent back to planning


async def test_completed_issue_reopen_lifecycle(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    """Test: completed issue -> still open (no reopen) -> closed -> reopened with comment -> fresh planning -> plan_review (no PR yet)"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
    dispatcher._planning.agent_service = agent_service
    dispatcher._planning.workspace_mgr = workspace_mgr
    dispatcher._planning.github = github

    # Setup: create a completed issue with an existing PR
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "Add feature", "body": "Details"})
    await db.update_issue_phase(issue_id, "completed")
    await db.update_issue_pr(issue_id, 5)
    await db.update_issue_branch(issue_id, "agent/issue-1")

    # Poll 1: Issue still open on GitHub — should NOT create reopen
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 0

    # Poll 2: Issue closed (not in open list) — should mark as closed
    github.list_issues.return_value = []
    github.get_pr_comments.return_value = [
        {"id": 50, "body": "thanks", "author": "testuser", "created_at": "2026-01-01"},
    ]
    await poller.poll_once()
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.issue_closed_seen is True
    assert issue.last_issue_comment_id == 50

    # Poll 3: Issue reopened with new comment — should create reopen event
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    github.get_pr_comments.return_value = [
        {"id": 50, "body": "thanks", "author": "testuser", "created_at": "2026-01-01"},
        {"id": 200, "body": "Actually, please also handle edge case X", "author": "testuser", "created_at": "2026-01-05"},
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0].event_type == "reopen"

    # Dispatcher processes reopen: closes old PR, clears state, runs planning
    github.close_pr = AsyncMock()
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "new123"
    agent_service.run_planning.return_value = AgentResult(
        success=True, session_id="s-reopen", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    # Verify old PR was closed
    github.close_pr.assert_called_once_with("owner", "repo", 5,
        comment="Issue reopened. Closing this PR in favor of a fresh one.")

    # Verify fresh state: no PR yet (planning posts plan to issue, not PR), branch regenerated
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "plan_review"
    assert issue.pr_number is None  # No new PR during planning — PR created during implementation
    assert issue.issue_closed_seen is False  # Reset

    # Verify force=True was used for branch
    workspace_mgr.ensure_branch.assert_called_with("/tmp/ws", "agent/issue-1", force=True)
