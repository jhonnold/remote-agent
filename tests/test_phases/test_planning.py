# tests/test_phases/test_planning.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from remote_agent.phases.planning import PlanningHandler
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.agent import AgentResult


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.workspace.base_dir = "/tmp/workspaces"
    return cfg


@pytest.fixture
def deps(config):
    return {
        "config": config,
        "db": AsyncMock(),
        "github": AsyncMock(),
        "agent_service": AsyncMock(),
        "workspace_mgr": AsyncMock(),
    }


@pytest.fixture
def handler(deps):
    return PlanningHandler(
        deps["config"], deps["db"], deps["github"],
        deps["agent_service"], deps["workspace_mgr"],
    )


@pytest.fixture
def planning_issue():
    return Issue(
        id=1, repo_owner="o", repo_name="r", issue_number=42,
        title="Add auth", body="Need OAuth2", phase="planning",
        branch_name="agent/issue-42", design_approved=True,
    )


@pytest.fixture
def planning_event():
    return Event(id=1, issue_id=1, event_type="revision_requested", payload={})


async def test_planning_reads_design_and_saves_plan_to_temp(handler, deps, planning_issue, planning_event, tmp_path):
    workspace = str(tmp_path / "ws")
    deps["workspace_mgr"].ensure_workspace.return_value = workspace

    # Create design doc in workspace
    design_dir = Path(workspace) / "docs" / "plans"
    design_dir.mkdir(parents=True, exist_ok=True)
    design_file = design_dir / "issue-42-design.md"
    design_file.write_text("# Design\nOAuth2 flow")

    # Agent writes plan file during run
    async def fake_run_planning(**kwargs):
        plan_dir = Path(kwargs["cwd"]) / "docs" / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / "issue-42-plan.md").write_text("# Plan\nStep 1: Implement OAuth2")
        return AgentResult(
            success=True, session_id="sess-1", cost_usd=1.0,
            input_tokens=100, output_tokens=200,
        )

    deps["agent_service"].run_planning.side_effect = fake_run_planning

    result = await handler.handle(planning_issue, planning_event)

    # Verify design_content was passed to run_planning
    call_kwargs = deps["agent_service"].run_planning.call_args.kwargs
    assert call_kwargs["design_content"] == "# Design\nOAuth2 flow"
    # No existing_plan or feedback params
    assert "existing_plan" not in call_kwargs
    assert "feedback" not in call_kwargs

    # Verify plan_path stored on issue
    deps["db"].set_plan_path.assert_called_once()
    plan_path = deps["db"].set_plan_path.call_args[0][1]
    assert "issue-42-plan.md" in plan_path
    assert ".plans" in plan_path
    # Plan file should exist at temp location
    assert Path(plan_path).exists()
    assert Path(plan_path).read_text() == "# Plan\nStep 1: Implement OAuth2"

    # Plan file should NOT exist in workspace anymore
    assert not (Path(workspace) / "docs" / "plans" / "issue-42-plan.md").exists()

    # Auto-transition event created
    deps["db"].create_event.assert_called_once_with(planning_issue.id, "revision_requested", {})

    # NO PR creation
    deps["github"].create_pr.assert_not_called()

    # NO comment posted
    deps["github"].post_comment.assert_not_called()

    # next_phase is implementing
    assert result.next_phase == "implementing"


async def test_planning_transitions_automatically(handler, deps, planning_issue, planning_event, tmp_path):
    workspace = str(tmp_path / "ws")
    deps["workspace_mgr"].ensure_workspace.return_value = workspace

    # Create design doc
    design_dir = Path(workspace) / "docs" / "plans"
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "issue-42-design.md").write_text("# Design\nSome design")

    # Agent writes plan
    async def fake_run_planning(**kwargs):
        plan_dir = Path(kwargs["cwd"]) / "docs" / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / "issue-42-plan.md").write_text("# Plan\nSome plan")
        return AgentResult(
            success=True, session_id="sess-2", cost_usd=0.5,
            input_tokens=50, output_tokens=100,
        )

    deps["agent_service"].run_planning.side_effect = fake_run_planning

    result = await handler.handle(planning_issue, planning_event)

    # Verify next_phase is "implementing" with no human interaction
    assert result.next_phase == "implementing"
    deps["github"].create_pr.assert_not_called()
    deps["github"].post_comment.assert_not_called()
    # Auto-transition event
    deps["db"].create_event.assert_called_once_with(planning_issue.id, "revision_requested", {})


async def test_planning_audit_records(deps, planning_issue, planning_event, tmp_path):
    audit = AsyncMock()
    handler = PlanningHandler(
        deps["config"], deps["db"], deps["github"],
        deps["agent_service"], deps["workspace_mgr"], audit=audit,
    )

    workspace = str(tmp_path / "ws")
    deps["workspace_mgr"].ensure_workspace.return_value = workspace

    # Create design doc
    design_dir = Path(workspace) / "docs" / "plans"
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "issue-42-design.md").write_text("# Design\nSome design")

    # Agent writes plan
    async def fake_run_planning(**kwargs):
        plan_dir = Path(kwargs["cwd"]) / "docs" / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / "issue-42-plan.md").write_text("# Plan\nSome plan")
        return AgentResult(
            success=True, session_id="sess-1", cost_usd=1.0,
            input_tokens=100, output_tokens=200,
        )

    deps["agent_service"].run_planning.side_effect = fake_run_planning

    result = await handler.handle(planning_issue, planning_event)

    assert result.next_phase == "implementing"
    # Verify audit was called
    assert audit.log.call_count >= 1
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "phase_transition" in categories
