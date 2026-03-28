# tests/test_phases/test_implementation.py
import pytest
from unittest.mock import AsyncMock, patch
from remote_agent.phases.implementation import ImplementationHandler
from remote_agent.models import Issue, Event
from remote_agent.agent import AgentResult


@pytest.fixture
def deps():
    return {"db": AsyncMock(), "github": AsyncMock(), "agent_service": AsyncMock(), "workspace_mgr": AsyncMock()}


@pytest.fixture
def handler(deps):
    return ImplementationHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])


@pytest.fixture
def impl_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="", phase="implementing",
                 pr_number=10, branch_name="agent/issue-42",
                 workspace_path="/tmp/ws",
                 plan_path="/tmp/.plans/issue-42-plan.md")


def _patch_path(monkeypatch, file_contents: dict[str, str] | None = None):
    """Patch Path.exists and Path.read_text to use a dict of path->content."""
    contents = file_contents or {}
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr("pathlib.Path.read_text", lambda self, **kw: contents.get(str(self), ""))


async def test_implementation_reads_design_and_plan(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="implementing",
                  branch_name="agent/issue-42",
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["github"].create_pr.return_value = 99
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s", cost_usd=2.0, input_tokens=500, output_tokens=1000,
    )
    with pytest.MonkeyPatch.context() as m:
        _patch_path(m, {
            "/tmp/ws/docs/plans/issue-42-design.md": "## Design content",
            "/tmp/.plans/issue-42-plan.md": "## Plan content",
        })
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    call_kwargs = deps["agent_service"].run_implementation.call_args.kwargs
    assert call_kwargs["design_content"] == "## Design content"
    assert call_kwargs["plan_content"] == "## Plan content"
    assert call_kwargs["issue_body"] == "Need OAuth2"


async def test_implementation_creates_pr_when_none_exists(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="implementing",
                  branch_name="agent/issue-42",
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["github"].create_pr.return_value = 55
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s", cost_usd=2.0, input_tokens=500, output_tokens=1000,
    )
    with pytest.MonkeyPatch.context() as m:
        _patch_path(m)
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    deps["github"].create_pr.assert_called_once_with(
        "o", "r",
        title="[Agent] Add auth",
        body="Implementation for #42\n\nCloses #42",
        branch="agent/issue-42",
        draft=False,
    )
    deps["db"].update_issue_pr.assert_called_once_with(1, 55)
    deps["github"].mark_pr_ready.assert_not_called()


async def test_implementation_skips_pr_creation_when_exists(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="implementing",
                  pr_number=10, branch_name="agent/issue-42",
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s", cost_usd=2.0, input_tokens=500, output_tokens=1000,
    )
    with pytest.MonkeyPatch.context() as m:
        _patch_path(m)
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    deps["github"].create_pr.assert_not_called()
    deps["github"].mark_pr_ready.assert_called_once_with("o", "r", 10)


async def test_implementation_publishes_pr(handler, deps, impl_issue):
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s", cost_usd=2.0, input_tokens=500, output_tokens=1000,
    )
    with pytest.MonkeyPatch.context() as m:
        _patch_path(m)
        result = await handler.handle(impl_issue, event)

    assert result.next_phase == "code_review"
    deps["github"].mark_pr_ready.assert_called_once()


async def test_implementation_audit_records(deps):
    audit = AsyncMock()
    handler = ImplementationHandler(deps["db"], deps["github"], deps["agent_service"],
                                     deps["workspace_mgr"], audit=audit)

    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="implementing",
                  pr_number=10, branch_name="agent/issue-42",
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with pytest.MonkeyPatch.context() as m:
        _patch_path(m)
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    assert audit.log.call_count >= 1
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "phase_transition" in categories


async def test_implementation_returns_error_when_plan_path_none(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="implementing",
                  branch_name="agent/issue-42",
                  plan_path=None)
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"

    result = await handler.handle(issue, event)
    assert result.next_phase == "error"
    assert "plan_path" in result.error_message.lower()


async def test_implementation_uses_llm_commit_message(handler, deps):
    """Verify that when the agent provides a <commit_message> tag, it's used."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="implementing",
                  branch_name="agent/issue-42", pr_number=10,
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s", cost_usd=2.0, input_tokens=500, output_tokens=1000,
        result_text="Done.\n<commit_message>feat: add OAuth2 endpoints</commit_message>",
    )

    with patch("remote_agent.phases.implementation.Path") as path_mock:
        path_mock.return_value.exists.return_value = True
        path_mock.return_value.read_text.return_value = "# Plan content"
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    deps["workspace_mgr"].commit_and_push.assert_called_once_with(
        "/tmp/ws", "agent/issue-42", "feat: add OAuth2 endpoints\n\nCloses #42",
    )


async def test_implementation_falls_back_on_none_result(handler, deps):
    """Verify fallback when agent doesn't provide a <commit_message> tag."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="implementing",
                  branch_name="agent/issue-42", pr_number=10,
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s", cost_usd=2.0, input_tokens=500, output_tokens=1000,
    )

    with patch("remote_agent.phases.implementation.Path") as path_mock:
        path_mock.return_value.exists.return_value = True
        path_mock.return_value.read_text.return_value = "# Plan content"
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    deps["workspace_mgr"].commit_and_push.assert_called_once_with(
        "/tmp/ws", "agent/issue-42", "feat: implement Add auth (#42)\n\nCloses #42",
    )


async def test_implementation_pr_body_includes_closes(handler, deps):
    """Verify that the PR body includes the Closes keyword for GitHub auto-closing."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="implementing",
                  branch_name="agent/issue-42",
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["github"].create_pr.return_value = 55
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s", cost_usd=2.0, input_tokens=500, output_tokens=1000,
    )

    with patch("remote_agent.phases.implementation.Path") as path_mock:
        path_mock.return_value.exists.return_value = True
        path_mock.return_value.read_text.return_value = "# Plan content"
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    call_kwargs = deps["github"].create_pr.call_args.kwargs
    assert "Closes #42" in call_kwargs["body"]
