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


def _override_dispatcher_handlers(dispatcher, agent_service, workspace_mgr, github):
    """Override all dispatcher handler dependencies with our mocks."""
    for handler_name in ("_designing", "_design_review", "_planning", "_implementation", "_code_review"):
        handler = getattr(dispatcher, handler_name)
        if hasattr(handler, "agent_service"):
            handler.agent_service = agent_service
        if hasattr(handler, "workspace_mgr"):
            handler.workspace_mgr = workspace_mgr
        if hasattr(handler, "github"):
            handler.github = github


async def test_full_lifecycle_happy_path(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    """Test: new issue -> designing -> design_review -> planning -> implementing -> code_review -> completed"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
    _override_dispatcher_handlers(dispatcher, agent_service, workspace_mgr, github)

    # Step 1: Poller detects new issue
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "new"

    # Step 2: Dispatcher routes to designing — creates branch, runs design agent, posts design comment
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "abc123"
    agent_service.run_designing.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "design_review"
    assert issue.design_commit_hash == "abc123"

    # Step 3: Human approves design (comment on issue, polled as issue comment)
    github.get_pr_comments.return_value = [
        {"id": 100, "body": "LGTM, looks good", "author": "testuser", "created_at": "2026-01-01"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "planning"
    assert issue.design_approved is True

    # Step 4: Planning runs (triggered by revision_requested event from design_review handler)
    # Planning reads design doc, runs planning agent, moves plan to temp storage, auto-transitions
    agent_service.run_planning.return_value = AgentResult(
        success=True, session_id="s2", cost_usd=0.5, input_tokens=200, output_tokens=300,
    )

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="## Design Doc"), \
         patch("pathlib.Path.mkdir"), \
         patch("shutil.move"):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "implementing"
    assert issue.plan_path is not None

    # Step 5: Implementation runs (triggered by revision_requested event from planning handler)
    # Implementation reads design doc + plan, runs implementation agent, creates PR
    agent_service.run_implementation.return_value = AgentResult(
        success=True, session_id="s3", cost_usd=3.0, input_tokens=500, output_tokens=1000,
    )
    github.create_pr.return_value = 5

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="## Plan Content"):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "code_review"
    assert issue.pr_number == 5

    # Step 6: Human approves code (comment on PR)
    github.get_pr_comments.return_value = [
        {"id": 200, "body": "Ship it!", "author": "testuser", "created_at": "2026-01-02"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")

    with patch("pathlib.Path.exists", return_value=False), \
         patch("pathlib.Path.unlink"):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "completed"
    assert issue.plan_path is None

    # Verify audit trail
    audit_lines = audit_file.read_text().strip().split("\n")
    audit_records = [json.loads(line) for line in audit_lines]
    categories_and_actions = [(r["category"], r["action"]) for r in audit_records]

    # Should have phase transition records for the full lifecycle
    assert ("phase_transition", "design_review") in categories_and_actions
    assert ("phase_transition", "planning") in categories_and_actions
    assert ("phase_transition", "implementing") in categories_and_actions
    assert ("phase_transition", "code_review") in categories_and_actions
    assert ("phase_transition", "completed") in categories_and_actions

    # Should also have comment classification records
    assert ("comment_classification", "approve") in categories_and_actions

    # All records should be successful
    assert all(r["success"] for r in audit_records)


async def test_review_comment_triggers_revision(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    """Test: design_review -> user submits comment with feedback -> revision -> designing"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
    _override_dispatcher_handlers(dispatcher, agent_service, workspace_mgr, github)

    # Setup: create issue and advance to design_review via designing
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "abc123"
    agent_service.run_designing.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    await poller.poll_once()
    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "design_review"

    # User submits a comment on the issue with feedback
    github.get_pr_comments.return_value = [
        {"id": 100, "body": "Please reconsider the approach for the auth module",
         "author": "testuser", "created_at": "2026-01-02"}
    ]

    await poller.poll_once()

    # Verify event was created
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1
    assert "reconsider" in comment_events[0].payload["body"]

    # Dispatcher routes to design_review handler, which classifies as revise
    agent_service.interpret_comment.return_value = CommentInterpretation(intent="revise")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "designing"  # Revision sent back to designing


async def test_completed_issue_reopen_lifecycle(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    """Test: completed issue -> closed -> reopened with comment -> fresh designing -> design_review"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
    _override_dispatcher_handlers(dispatcher, agent_service, workspace_mgr, github)

    # Setup: create a completed issue with an existing PR
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "Add feature", "body": "Details"})
    await db.update_issue_phase(issue_id, "completed")
    await db.update_issue_pr(issue_id, 5)
    await db.update_issue_branch(issue_id, "agent/issue-1")
    await db.set_design_approved(issue_id, True)

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

    # Dispatcher processes reopen: closes old PR, clears state (design_approved, plan_path), runs designing
    github.close_pr = AsyncMock()
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "new123"
    agent_service.run_designing.return_value = AgentResult(
        success=True, session_id="s-reopen", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    # Verify old PR was closed
    github.close_pr.assert_called_once_with("owner", "repo", 5,
        comment="Issue reopened. Closing this PR in favor of a fresh one.")

    # Verify fresh state: design_review, design_approved cleared
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "design_review"
    assert issue.design_approved is False
    assert issue.issue_closed_seen is False  # Reset
    assert issue.design_commit_hash == "new123"

    # Verify force=True was used for branch (branch_name was cleared on reopen)
    workspace_mgr.ensure_branch.assert_called_with("/tmp/ws", "agent/issue-1", force=True)
