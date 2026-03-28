# src/remote_agent/phases/designing.py
from __future__ import annotations
import logging
from pathlib import Path

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class DesigningHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        logger.info("Handling designing for issue %d", issue.id)

        # 1. Ensure workspace
        workspace = await self.workspace_mgr.ensure_workspace(
            issue.repo_owner, issue.repo_name, issue.issue_number,
        )
        await self.db.update_issue_workspace(issue.id, workspace)

        # 2. Determine branch
        branch = issue.branch_name or f"agent/issue-{issue.issue_number}"
        force = issue.branch_name is None
        await self.workspace_mgr.ensure_branch(workspace, branch, force=force)
        await self.db.update_issue_branch(issue.id, branch)

        # 3. Read existing design doc if revision
        existing_design = None
        design_path = Path(workspace) / "docs" / "plans" / f"issue-{issue.issue_number}-design.md"
        if design_path.exists():
            existing_design = design_path.read_text()

        # 4. Extract feedback from event payload
        feedback = None
        if event.event_type in ("revision_requested", "new_comment"):
            feedback = event.payload.get("body")

        # 5. Run designing agent
        await self.agent_service.run_designing(
            issue_number=issue.issue_number,
            issue_title=issue.title,
            issue_body=issue.body or "",
            cwd=workspace,
            issue_id=issue.id,
            existing_design=existing_design,
            feedback=feedback,
        )

        # 6. Commit and push
        commit_msg = "docs: design for issue #{}".format(issue.issue_number)
        if existing_design:
            commit_msg = "docs: revise design for issue #{}".format(issue.issue_number)
        await self.workspace_mgr.commit_and_push(workspace, branch, commit_msg)

        # 7. Store design commit hash
        design_commit = await self.workspace_mgr.get_head_commit(workspace)
        await self.db.set_design_commit_hash(issue.id, design_commit)

        # 8. Post design as issue comment (NOT PR comment)
        post_design_path = Path(workspace) / "docs" / "plans" / f"issue-{issue.issue_number}-design.md"
        design_content = ""
        if post_design_path.exists():
            design_content = post_design_path.read_text()

        if not design_content.strip():
            return PhaseResult(next_phase="error",
                               error_message="Designing agent did not produce a design document")

        comment_body = (
            "## Design Document\n\n"
            f"{design_content}\n\n"
            "---\n"
            "Please review this design and comment with your feedback. "
            "Reply with approval to proceed to planning, or request changes."
        )
        await self.github.post_comment(
            issue.repo_owner, issue.repo_name, issue.issue_number, comment_body,
        )

        # 9. Audit
        logger.info("Completed designing for issue %d", issue.id)
        if self.audit:
            await self.audit.log("phase_transition", "design_review",
                                  issue_id=issue.id, success=True)

        # 10. Return next phase
        return PhaseResult(next_phase="design_review")
