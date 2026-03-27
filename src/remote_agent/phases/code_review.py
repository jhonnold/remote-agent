# src/remote_agent/phases/code_review.py
from __future__ import annotations
import logging

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

        interpretation = await self.agent_service.interpret_comment(
            comment=comment_body, context="code_review",
            issue_title=issue.title, issue_id=issue.id,
        )
        logger.info("Code review comment interpreted as: %s", interpretation.intent)
        if self.audit:
            await self.audit.log(
                "comment_classification", interpretation.intent,
                issue_id=issue.id, success=True,
            )

        if interpretation.intent == "approve":
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

        elif interpretation.intent == "back_to_planning":
            await self.db.set_plan_approved(issue.id, False)
            await self.github.close_pr(
                issue.repo_owner, issue.repo_name, issue.pr_number,
                comment="Going back to planning. Will create a new PR after re-implementation.",
            )
            await self.db.update_issue_pr(issue.id, None)
            if issue.plan_commit_hash and issue.workspace_path:
                await self.workspace_mgr.reset_to_commit(
                    issue.workspace_path, issue.plan_commit_hash, issue.branch_name,
                )
            await self.db.create_event(issue.id, "revision_requested", event.payload)
            return PhaseResult(next_phase="planning")

        elif interpretation.intent == "question":
            response = interpretation.response or "I'll look into that."
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.pr_number, response,
            )
            return PhaseResult(next_phase="code_review")

        return PhaseResult(next_phase="code_review")
