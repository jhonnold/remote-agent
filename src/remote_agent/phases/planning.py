# src/remote_agent/phases/planning.py
from __future__ import annotations
import logging
import shutil
from pathlib import Path

from remote_agent.config import Config
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class PlanningHandler:
    def __init__(self, config: Config, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        self.config = config
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        logger.info("Handling planning for issue %d", issue.id)

        # 1. Ensure workspace + branch (branch already exists from designing phase)
        workspace = await self.workspace_mgr.ensure_workspace(
            issue.repo_owner, issue.repo_name, issue.issue_number,
        )
        await self.db.update_issue_workspace(issue.id, workspace)

        branch = issue.branch_name or f"agent/issue-{issue.issue_number}"
        force = issue.branch_name is None
        await self.workspace_mgr.ensure_branch(workspace, branch, force=force)
        await self.db.update_issue_branch(issue.id, branch)

        # 2. Read the design doc from the branch
        design_path = Path(workspace) / "docs" / "plans" / f"issue-{issue.issue_number}-design.md"
        design_content = ""
        if design_path.exists():
            design_content = design_path.read_text()

        # 3. Run planning agent
        await self.agent_service.run_planning(
            issue_number=issue.issue_number,
            issue_title=issue.title,
            issue_body=issue.body or "",
            design_content=design_content,
            cwd=workspace,
            issue_id=issue.id,
        )

        # 4. Move plan from workspace to temp storage
        plan_filename = f"issue-{issue.issue_number}-plan.md"
        ws_plan_path = Path(workspace) / "docs" / "plans" / plan_filename
        plans_dir = Path(workspace).parent / ".plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        temp_plan_path = plans_dir / plan_filename

        if ws_plan_path.exists():
            shutil.move(str(ws_plan_path), str(temp_plan_path))
            await self.db.set_plan_path(issue.id, str(temp_plan_path))
        else:
            return PhaseResult(next_phase="error",
                               error_message=f"Planning agent did not produce plan at {ws_plan_path}")

        # 6. Auto-transition to implementing (no human gate)
        await self.db.create_event(issue.id, "revision_requested", {})

        # 7. Audit
        logger.info("Completed planning for issue %d", issue.id)
        if self.audit:
            await self.audit.log("phase_transition", "implementing",
                                  issue_id=issue.id, success=True)

        # 8. Return next phase
        return PhaseResult(next_phase="implementing")
