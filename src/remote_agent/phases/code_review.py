# src/remote_agent/phases/code_review.py
from __future__ import annotations
import logging
from pathlib import Path

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class CodeReviewHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        comment_body = event.payload.get("body", "")

        # Read design doc and plan for context
        design_content = ""
        plan_content = ""
        if issue.workspace_path:
            design_path = Path(issue.workspace_path) / "docs" / "plans" / f"issue-{issue.issue_number}-design.md"
            if design_path.exists():
                design_content = design_path.read_text()
        if issue.plan_path:
            plan_file = Path(issue.plan_path)
            if plan_file.exists():
                plan_content = plan_file.read_text()

        interpretation = await self.agent_service.interpret_comment(
            comment=comment_body, context="code_review",
            issue_title=issue.title, issue_id=issue.id,
            design_content=design_content,
            plan_content=plan_content,
        )
        logger.info("Code review comment interpreted as: %s", interpretation.intent)
        if self.audit:
            await self.audit.log(
                "comment_classification", interpretation.intent,
                issue_id=issue.id, success=True,
            )

        if interpretation.intent == "approve":
            # Clean temp plan
            await self.db.clear_plan_path(issue.id)
            if issue.plan_path:
                Path(issue.plan_path).unlink(missing_ok=True)
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.pr_number,
                "Code approved! The PR is ready for you to merge.",
            )
            self.workspace_mgr.cleanup(issue.repo_owner, issue.repo_name, issue.issue_number)
            if self.audit:
                await self.audit.log("phase_transition", "completed",
                                      issue_id=issue.id, success=True)
            return PhaseResult(next_phase="completed")

        elif interpretation.intent == "revise":
            await self.db.create_event(issue.id, "revision_requested", event.payload)
            return PhaseResult(next_phase="implementing")

        elif interpretation.intent == "back_to_design":
            # Non-destructive operations first
            await self.github.mark_pr_draft(issue.repo_owner, issue.repo_name, issue.pr_number)
            await self.db.set_design_approved(issue.id, False)
            await self.db.clear_plan_path(issue.id)
            # Post feedback on ISSUE (not PR) - design review lives on the issue
            feedback_text = (
                f"Code review feedback requests design changes:\n\n> {comment_body}\n\n"
                "Returning to design phase for revision."
            )
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.issue_number, feedback_text,
            )
            await self.db.create_event(issue.id, "revision_requested", event.payload)
            # Destructive operations last
            if issue.design_commit_hash:
                await self.workspace_mgr.reset_to_commit(
                    issue.workspace_path, issue.design_commit_hash, issue.branch_name,
                )
            if issue.plan_path:
                Path(issue.plan_path).unlink(missing_ok=True)
            return PhaseResult(next_phase="designing")

        elif interpretation.intent == "question":
            answer = await self.agent_service.answer_question(
                question=comment_body, context="code_review",
                issue_title=issue.title, issue_body=issue.body or "",
                issue_id=issue.id,
                design_content=design_content,
                plan_content=plan_content,
            )
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.pr_number, answer,
            )
            return PhaseResult(next_phase="code_review")

        return PhaseResult(next_phase="code_review")
