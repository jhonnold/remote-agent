# src/remote_agent/phases/implementation.py
from __future__ import annotations
import logging
from pathlib import Path

from remote_agent.commit_message import extract_commit_message, build_commit_message
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class ImplementationHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        logger.info("Handling implementation for issue %d", issue.id)
        workspace = await self.workspace_mgr.ensure_workspace(
            issue.repo_owner, issue.repo_name, issue.issue_number,
        )
        await self.workspace_mgr.ensure_branch(workspace, issue.branch_name)

        # Read design doc from the branch
        design_path = Path(workspace) / "docs" / "plans" / f"issue-{issue.issue_number}-design.md"
        design_content = design_path.read_text() if design_path.exists() else ""

        # Read plan from temp storage via issue.plan_path
        if not issue.plan_path:
            return PhaseResult(next_phase="error", error_message="No plan_path set on issue")
        plan_file = Path(issue.plan_path)
        if not plan_file.exists():
            return PhaseResult(next_phase="error", error_message="Plan file not found at plan_path")
        plan_content = plan_file.read_text()

        feedback = event.payload.get("body")
        is_revision = bool(feedback)

        result = await self.agent_service.run_implementation(
            plan_content=plan_content,
            design_content=design_content,
            issue_title=issue.title,
            issue_body=issue.body or "",
            cwd=workspace,
            issue_id=issue.id,
            feedback=feedback,
        )

        extracted = extract_commit_message(result.result_text)
        commit_msg = build_commit_message(
            extracted, issue.issue_number, issue.title,
            closes=True, is_revision=is_revision,
        )
        await self.workspace_mgr.commit_and_push(workspace, issue.branch_name, commit_msg)

        # Create PR if none exists, otherwise mark it ready (revision from code_review)
        if issue.pr_number is None:
            pr_number = await self.github.create_pr(
                issue.repo_owner, issue.repo_name,
                title=f"[Agent] {issue.title}",
                body=f"Implementation for #{issue.issue_number}\n\nCloses #{issue.issue_number}",
                branch=issue.branch_name,
                draft=False,
            )
            await self.db.update_issue_pr(issue.id, pr_number)
        else:
            pr_number = issue.pr_number
            await self.github.mark_pr_ready(issue.repo_owner, issue.repo_name, pr_number)

        await self.github.post_comment(
            issue.repo_owner, issue.repo_name, pr_number,
            "Implementation complete. Please review the code and comment with feedback.",
        )

        logger.info("Completed implementation for issue %d", issue.id)
        if self.audit:
            await self.audit.log("phase_transition", "code_review",
                                  issue_id=issue.id, success=True)
        return PhaseResult(next_phase="code_review")
