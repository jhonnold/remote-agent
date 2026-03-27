# tests/test_dispatcher.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from remote_agent.dispatcher import Dispatcher
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.config import AgentConfig
from remote_agent.logging_config import current_issue_id


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.agent = AgentConfig(daily_budget_usd=50.0)
    return config


@pytest.fixture
def deps():
    return {
        "db": AsyncMock(),
        "github": AsyncMock(),
        "agent_service": AsyncMock(),
        "workspace_mgr": AsyncMock(),
    }


@pytest.fixture
def dispatcher(mock_config, deps):
    return Dispatcher(mock_config, deps["db"], deps["github"],
                      deps["agent_service"], deps["workspace_mgr"])


async def test_routes_new_issue_to_planning(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="new")
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    # Mock the planning handler
    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.return_value = PhaseResult(next_phase="plan_review")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    deps["db"].update_issue_phase.assert_called_once_with(1, "plan_review")
    deps["db"].mark_event_processed.assert_called_once_with(1)


async def test_budget_exceeded_leaves_event_unprocessed(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="new")
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 100.0  # Over budget

    await dispatcher.process_events()

    deps["db"].mark_event_processed.assert_not_called()
    deps["github"].post_comment.assert_called_once()  # Budget notification
    deps["db"].set_budget_notified.assert_called_once()


async def test_handler_error_transitions_to_error(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="new")
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.side_effect = Exception("Agent crashed")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    deps["db"].update_issue_phase.assert_called_with(1, "error")
    deps["db"].mark_event_processed.assert_called_once()


async def test_recover_interrupted_issues(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="planning")
    deps["db"].get_active_issues.return_value = [issue]
    deps["db"].get_unprocessed_events.return_value = []  # No events pending

    await dispatcher.recover_interrupted_issues()

    deps["db"].update_issue_phase.assert_called_once_with(1, "error")
    deps["db"].update_issue_error.assert_called_once()


async def test_context_vars_isolated_per_event(mock_config, deps):
    """Verify ContextVar isolation: each event gets its own issue_id context."""
    captured = {}

    async def capturing_handle(issue, event):
        captured[event.id] = current_issue_id.get(None)
        return PhaseResult(next_phase="plan_review")

    issue1 = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                   title="T", body="", phase="new")
    issue2 = Issue(id=2, repo_owner="o", repo_name="r", issue_number=2,
                   title="T2", body="", phase="new")
    event1 = Event(id=10, issue_id=1, event_type="new_issue", payload={})
    event2 = Event(id=20, issue_id=2, event_type="new_issue", payload={})

    deps["db"].get_unprocessed_events.return_value = [event1, event2]
    deps["db"].get_issue_by_id.side_effect = lambda id: {1: issue1, 2: issue2}[id]
    deps["db"].get_daily_spend.return_value = 0.0

    dispatcher = Dispatcher(mock_config, deps["db"], deps["github"],
                            deps["agent_service"], deps["workspace_mgr"])

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.side_effect = capturing_handle
        mock_handler.return_value = handler
        await dispatcher.process_events()

    assert captured[10] == 1
    assert captured[20] == 2


async def test_reopen_closes_old_pr_and_clears_state(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="completed", pr_number=5,
                  branch_name="agent/issue-1", plan_commit_hash="abc123",
                  issue_closed_seen=True)
    event = Event(id=1, issue_id=1, event_type="reopen",
                  payload={"body": "Please redo this"})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.return_value = PhaseResult(next_phase="plan_review")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    deps["github"].close_pr.assert_called_once_with("o", "r", 5,
        comment="Issue reopened. Closing this PR in favor of a fresh one.")
    deps["db"].set_plan_approved.assert_called_once_with(1, False)
    deps["db"].clear_issue_for_reopen.assert_called_once_with(1)


async def test_error_comment_posted_to_issue_when_no_pr(mock_config, deps):
    """When an issue has no PR, error comments should go to the issue number."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="T", body="", phase="new", pr_number=None)
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    dispatcher = Dispatcher(mock_config, deps["db"], deps["github"],
                            deps["agent_service"], deps["workspace_mgr"])

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.side_effect = RuntimeError("boom")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    # Should post to issue_number (42) since pr_number is None
    deps["github"].post_comment.assert_called_once()
    assert deps["github"].post_comment.call_args[0][2] == 42


async def test_error_path_calls_audit(mock_config, deps):
    audit = AsyncMock()
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="new")
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    dispatcher = Dispatcher(mock_config, deps["db"], deps["github"],
                            deps["agent_service"], deps["workspace_mgr"], audit=audit)

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.side_effect = Exception("crash")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    audit.log.assert_called_once()
    call_kwargs = audit.log.call_args.kwargs
    assert call_kwargs["success"] is False
