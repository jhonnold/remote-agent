# src/remote_agent/phases/plan_review.py
from __future__ import annotations
import logging

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService

logger = logging.getLogger(__name__)


class PlanReviewHandler:
    def __init__(self, db: Database, github: GitHubService, agent_service: AgentService,
                 audit=None):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        comment_body = event.payload.get("body", "")

        interpretation = await self.agent_service.interpret_comment(
            comment=comment_body, context="plan_review",
            issue_title=issue.title, issue_id=issue.id,
        )
        logger.info("Plan review comment interpreted as: %s", interpretation.intent)
        if self.audit:
            await self.audit.log(
                "comment_classification", interpretation.intent,
                issue_id=issue.id, success=True,
            )

        if interpretation.intent == "approve":
            await self.db.set_plan_approved(issue.id, True)
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.pr_number,
                "Plan approved. Starting implementation...",
            )
            # Create event to drive the implementation handler
            await self.db.create_event(issue.id, "revision_requested", {})
            if self.audit:
                await self.audit.log("phase_transition", "implementing",
                                      issue_id=issue.id, success=True)
            return PhaseResult(next_phase="implementing")

        elif interpretation.intent == "revise":
            await self.db.create_event(issue.id, "revision_requested", event.payload)
            return PhaseResult(next_phase="planning")

        elif interpretation.intent == "question":
            response = interpretation.response or "I'll look into that."
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.pr_number, response,
            )
            return PhaseResult(next_phase="plan_review")

        return PhaseResult(next_phase="plan_review")
