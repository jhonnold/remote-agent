# src/remote_agent/phases/implementation.py
from __future__ import annotations
import logging
from pathlib import Path

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class ImplementationHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        workspace = await self.workspace_mgr.ensure_workspace(
            issue.repo_owner, issue.repo_name, issue.issue_number,
        )
        await self.workspace_mgr.ensure_branch(workspace, issue.branch_name)

        plan_path = Path(workspace) / "docs" / "plans" / f"issue-{issue.issue_number}-plan.md"
        if not plan_path.exists():
            return PhaseResult(next_phase="error", error_message="Plan file not found")
        plan_content = plan_path.read_text()

        feedback = event.payload.get("body") if event.event_type in ("revision_requested", "new_comment") else None

        await self.agent_service.run_implementation(
            plan_content=plan_content,
            issue_title=issue.title,
            cwd=workspace,
            issue_id=issue.id,
            feedback=feedback,
        )

        commit_msg = f"feat: implement plan for issue #{issue.issue_number}"
        if feedback:
            commit_msg = f"fix: address review feedback for issue #{issue.issue_number}"
        await self.workspace_mgr.commit_and_push(workspace, issue.branch_name, commit_msg)

        await self.github.mark_pr_ready(issue.repo_owner, issue.repo_name, issue.pr_number)

        await self.github.post_comment(
            issue.repo_owner, issue.repo_name, issue.pr_number,
            "Implementation complete. Please review the code and comment with feedback.",
        )

        return PhaseResult(next_phase="code_review")
