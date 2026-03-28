# tests/test_phases/test_designing.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from remote_agent.phases.designing import DesigningHandler
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.agent import AgentResult


def _make_path_mock(exists: bool, content: str = "") -> MagicMock:
    """Create a mock that behaves like Path(x) / "docs" / "plans" / "file.md"."""
    final = MagicMock()
    final.exists.return_value = exists
    if exists:
        final.read_text.return_value = content
    plans = MagicMock()
    plans.__truediv__ = MagicMock(return_value=final)
    docs = MagicMock()
    docs.__truediv__ = MagicMock(return_value=plans)
    root = MagicMock()
    root.__truediv__ = MagicMock(return_value=docs)
    return root


@pytest.fixture
def deps():
    return {
        "db": AsyncMock(),
        "github": AsyncMock(),
        "agent_service": AsyncMock(),
        "workspace_mgr": AsyncMock(),
    }


@pytest.fixture
def handler(deps):
    return DesigningHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])


@pytest.fixture
def new_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="Need OAuth2", phase="new")


@pytest.fixture
def new_issue_event():
    return Event(id=1, issue_id=1, event_type="new_issue",
                 payload={"number": 42, "title": "Add auth", "body": "Need OAuth2"})


async def test_designing_creates_branch_and_posts_design(handler, deps, new_issue, new_issue_event):
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_designing.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    # Path is constructed twice: once before agent (no existing design), once after (to read for comment)
    path_mocks = [
        _make_path_mock(exists=False),
        _make_path_mock(exists=True, content="# Design\nSome design content"),
    ]
    with patch("remote_agent.phases.designing.Path", side_effect=path_mocks):
        result = await handler.handle(new_issue, new_issue_event)

    assert result.next_phase == "design_review"
    # Branch created with force=True (new issue, no branch_name)
    deps["workspace_mgr"].ensure_branch.assert_called_once_with("/tmp/ws", "agent/issue-42", force=True)
    # Design committed
    deps["workspace_mgr"].commit_and_push.assert_called_once_with(
        "/tmp/ws", "agent/issue-42", "docs: design for issue #42",
    )
    # Design commit hash stored
    deps["db"].set_design_commit_hash.assert_called_once_with(1, "abc123")
    # Comment posted on ISSUE (issue_number=42), NOT on a PR
    deps["github"].post_comment.assert_called_once()
    call_args = deps["github"].post_comment.call_args
    assert call_args[0][0] == "o"   # owner
    assert call_args[0][1] == "r"   # repo
    assert call_args[0][2] == 42    # issue_number, NOT pr_number


async def test_designing_revision_passes_feedback(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="designing",
                  branch_name="agent/issue-42")
    event = Event(id=2, issue_id=1, event_type="revision_requested",
                  payload={"body": "Change approach to use JWT"})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "def456"
    deps["agent_service"].run_designing.return_value = AgentResult(
        success=True, session_id="sess-2", cost_usd=0.5, input_tokens=50, output_tokens=100,
    )

    path_mocks = [
        _make_path_mock(exists=True, content="# Old Design\nPrevious content"),
        _make_path_mock(exists=True, content="# Revised Design\nNew content"),
    ]
    with patch("remote_agent.phases.designing.Path", side_effect=path_mocks):
        result = await handler.handle(issue, event)

    assert result.next_phase == "design_review"
    # Ensure branch NOT forced (existing branch)
    deps["workspace_mgr"].ensure_branch.assert_called_once_with("/tmp/ws", "agent/issue-42", force=False)
    # run_designing called with existing_design and feedback
    deps["agent_service"].run_designing.assert_called_once()
    call_kwargs = deps["agent_service"].run_designing.call_args[1]
    assert call_kwargs["existing_design"] == "# Old Design\nPrevious content"
    assert call_kwargs["feedback"] == "Change approach to use JWT"
    # Commit message says "revise"
    deps["workspace_mgr"].commit_and_push.assert_called_once_with(
        "/tmp/ws", "agent/issue-42", "docs: revise design for issue #42",
    )


async def test_designing_audit_records(deps, new_issue, new_issue_event):
    audit = AsyncMock()
    handler = DesigningHandler(deps["db"], deps["github"], deps["agent_service"],
                                deps["workspace_mgr"], audit=audit)

    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_designing.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    path_mocks = [
        _make_path_mock(exists=False),
        _make_path_mock(exists=True, content="# Design\nContent"),
    ]
    with patch("remote_agent.phases.designing.Path", side_effect=path_mocks):
        result = await handler.handle(new_issue, new_issue_event)

    assert result.next_phase == "design_review"
    # Verify audit was called for phase transition
    assert audit.log.call_count >= 1
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "phase_transition" in categories
