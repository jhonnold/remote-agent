# src/remote_agent/dispatcher.py
from __future__ import annotations
import asyncio
import contextvars
import logging

from remote_agent.logging_config import current_issue_id, current_event_id

from remote_agent.config import Config
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.phases.planning import PlanningHandler
from remote_agent.phases.plan_review import PlanReviewHandler
from remote_agent.phases.implementation import ImplementationHandler
from remote_agent.phases.code_review import CodeReviewHandler

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, config: Config, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        self.config = config
        self.db = db
        self.github = github
        self.audit = audit
        self._planning = PlanningHandler(db, github, agent_service, workspace_mgr, audit=audit)
        self._plan_review = PlanReviewHandler(db, github, agent_service, audit=audit)
        self._implementation = ImplementationHandler(db, github, agent_service, workspace_mgr, audit=audit)
        self._code_review = CodeReviewHandler(db, github, agent_service, workspace_mgr, audit=audit)

    async def process_events(self):
        events = await self.db.get_unprocessed_events()
        logger.debug("Fetched %d events to process", len(events))
        for event in events:
            ctx = contextvars.copy_context()
            ctx.run(current_issue_id.set, event.issue_id)
            ctx.run(current_event_id.set, event.id)
            await asyncio.create_task(
                self._process_event(event), context=ctx
            )

    async def recover_interrupted_issues(self):
        active = await self.db.get_active_issues()
        events = await self.db.get_unprocessed_events()
        active_with_events = {e.issue_id for e in events}
        recovered = 0
        for issue in active:
            if issue.id not in active_with_events:
                logger.warning("Recovering interrupted issue #%d (was in %s)",
                              issue.issue_number, issue.phase)
                await self.db.update_issue_phase(issue.id, "error")
                await self.db.update_issue_error(issue.id, "Interrupted by restart")
                recovered += 1
        if recovered:
            logger.info("Recovered %d interrupted issues on startup", recovered)

    async def _process_event(self, event: Event):
        issue = await self.db.get_issue_by_id(event.issue_id)
        if not issue:
            await self.db.mark_event_processed(event.id)
            return

        handler = self._get_handler(issue, event)
        if not handler:
            await self.db.mark_event_processed(event.id)
            return

        target_phase = self._determine_target_phase(issue, event)
        if target_phase in ("planning", "implementing"):
            daily_spend = await self.db.get_daily_spend()
            if daily_spend >= self.config.agent.daily_budget_usd:
                if not issue.budget_notified:
                    target = issue.pr_number or issue.issue_number
                    try:
                        await self.github.post_comment(
                            issue.repo_owner, issue.repo_name, target,
                            "Daily budget limit reached. Will resume when budget resets.",
                        )
                    except Exception:
                        logger.exception("Failed to post budget notification")
                    await self.db.set_budget_notified(issue.id, True)
                return  # Leave event unprocessed

        # Close old PR and reset state on reopen events
        if event.event_type == "reopen":
            if issue.pr_number:
                try:
                    await self.github.close_pr(
                        issue.repo_owner, issue.repo_name, issue.pr_number,
                        comment="Issue reopened. Closing this PR in favor of a fresh one.",
                    )
                except Exception:
                    logger.exception("Failed to close old PR #%d", issue.pr_number)
            await self.db.set_plan_approved(issue.id, False)
            await self.db.clear_issue_for_reopen(issue.id)

        logger.info("Processing event %d: issue #%d (%s -> %s)",
                    event.id, issue.issue_number, issue.phase, target_phase)

        try:
            result = await handler.handle(issue, event)
            await self.db.update_issue_phase(issue.id, result.next_phase)
            if result.error_message:
                await self.db.update_issue_error(issue.id, result.error_message)
            # Reset budget notification on successful processing
            if issue.budget_notified:
                await self.db.set_budget_notified(issue.id, False)
        except Exception as e:
            logger.exception("Error processing event %d for issue #%d", event.id, issue.issue_number)
            await self.db.update_issue_phase(issue.id, "error")
            await self.db.update_issue_error(issue.id, str(e))
            if self.audit:
                await self.audit.log(
                    "phase_transition", "error",
                    issue_id=issue.id, event_id=event.id,
                    success=False, error_message=str(e),
                )
            target = issue.pr_number or issue.issue_number
            try:
                await self.github.post_comment(
                    issue.repo_owner, issue.repo_name, target,
                    f"Agent encountered an error:\n```\n{str(e)}\n```\nComment 'retry' to try again.",
                )
            except Exception:
                logger.exception("Failed to post error comment")
        finally:
            await self.db.mark_event_processed(event.id)

    def _get_handler(self, issue: Issue, event: Event):
        target = self._determine_target_phase(issue, event)
        if target == "planning":
            return self._planning
        elif target == "plan_review":
            return self._plan_review
        elif target == "implementing":
            return self._implementation
        elif target == "code_review":
            return self._code_review
        return None

    def _determine_target_phase(self, issue: Issue, event: Event) -> str | None:
        if event.event_type == "new_issue" and issue.phase == "new":
            return "planning"
        if event.event_type == "reopen":
            return "planning"
        if event.event_type == "revision_requested":
            # Target phase encoded in the context of who created the event
            if issue.phase in ("planning", "plan_review"):
                return "planning"
            if issue.phase in ("implementing", "code_review"):
                return "implementing" if issue.plan_approved else "planning"
            return "planning"
        if event.event_type == "new_comment":
            if issue.phase == "plan_review":
                return "plan_review"
            if issue.phase == "code_review":
                return "code_review"
            if issue.phase == "error":
                return "implementing" if issue.plan_approved else "planning"
        return None
