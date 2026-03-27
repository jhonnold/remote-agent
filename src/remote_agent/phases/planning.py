# src/remote_agent/phases/planning.py
from __future__ import annotations
import logging
from pathlib import Path

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class PlanningHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        logger.info("Handling planning for issue %d", issue.id)
        workspace = await self.workspace_mgr.ensure_workspace(
            issue.repo_owner, issue.repo_name, issue.issue_number,
        )
        await self.db.update_issue_workspace(issue.id, workspace)

        branch = issue.branch_name or f"agent/issue-{issue.issue_number}"
        force = issue.branch_name is None
        await self.workspace_mgr.ensure_branch(workspace, branch, force=force)
        await self.db.update_issue_branch(issue.id, branch)

        # Read existing plan if revision
        existing_plan = None
        plan_path = Path(workspace) / "docs" / "plans" / f"issue-{issue.issue_number}-plan.md"
        if plan_path.exists():
            existing_plan = plan_path.read_text()

        feedback = event.payload.get("body") if event.event_type in ("revision_requested", "new_comment") else None

        await self.agent_service.run_planning(
            issue_number=issue.issue_number,
            issue_title=issue.title,
            issue_body=issue.body or "",
            cwd=workspace,
            issue_id=issue.id,
            existing_plan=existing_plan,
            feedback=feedback,
        )

        commit_msg = "docs: plan for issue #{}".format(issue.issue_number)
        if existing_plan:
            commit_msg = "docs: revise plan for issue #{}".format(issue.issue_number)
        await self.workspace_mgr.commit_and_push(workspace, branch, commit_msg)

        plan_commit = await self.workspace_mgr.get_head_commit(workspace)
        await self.db.set_plan_commit_hash(issue.id, plan_commit)

        # Read the plan content to post as issue comment
        # The plan file should always exist after commit_and_push above.
        # This fallback is defensive and should not be reached in practice.
        plan_content = plan_path.read_text() if plan_path.exists() else "Plan file created."

        comment_body = (
            "## Plan\n\n"
            f"{plan_content}\n\n"
            "---\n"
            "*Review the plan above and comment with feedback, or approve to start implementation.*"
        )

        await self.github.post_comment(
            issue.repo_owner, issue.repo_name, issue.issue_number,
            comment_body,
        )

        logger.info("Completed planning for issue %d", issue.id)
        if self.audit:
            await self.audit.log("phase_transition", "plan_review",
                                  issue_id=issue.id, success=True)
        return PhaseResult(next_phase="plan_review")
