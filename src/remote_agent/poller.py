# src/remote_agent/poller.py
from __future__ import annotations
import logging

from remote_agent.config import Config
from remote_agent.db import Database
from remote_agent.github import GitHubService

logger = logging.getLogger(__name__)


class Poller:
    def __init__(self, config: Config, db: Database, github: GitHubService):
        self.config = config
        self.db = db
        self.github = github

    async def poll_once(self):
        for repo in self.config.repos:
            try:
                await self._poll_repo(repo.owner, repo.name)
            except Exception:
                logger.exception("Error polling %s/%s", repo.owner, repo.name)

    async def _poll_repo(self, owner: str, name: str):
        # 1. Check for new/reopened issues
        issues = await self.github.list_issues(owner, name, self.config.trigger.label)
        open_numbers = set()

        for issue_data in issues:
            open_numbers.add(issue_data["number"])
            author = issue_data.get("author", {}).get("login", "")
            if author not in self.config.users:
                continue

            existing = await self.db.get_issue(owner, name, issue_data["number"])
            if not existing:
                issue_id = await self.db.create_issue(owner, name, issue_data)
                if issue_id:
                    await self.db.create_event(issue_id, "new_issue", issue_data)
                    logger.info("New issue detected: %s/%s#%d", owner, name, issue_data["number"])
            elif existing.phase in ("completed", "error") and existing.issue_closed_seen:
                # Genuine reopen candidate — check for new issue comment
                await self._check_reopen(owner, name, existing)

        # 2. Detect closed completed/error issues
        done_issues = await self.db.get_completed_or_error_issues(owner, name)
        for issue in done_issues:
            if issue.issue_number not in open_numbers and not issue.issue_closed_seen:
                await self._snapshot_and_mark_closed(owner, name, issue)

        # 3. Check for new comments on issues in review or error phases
        review_issues = await self.db.get_issues_awaiting_comment(owner, name)
        for issue in review_issues:
            if issue.phase == "design_review":
                # Poll issue comments for design review (no PR exists yet)
                try:
                    comments = await self.github.get_pr_comments(owner, name, issue.issue_number)
                except Exception:
                    logger.exception("Error fetching issue comments for #%d", issue.issue_number)
                    continue
                new_comments = [c for c in comments if c["id"] > issue.last_issue_comment_id]
                new_comments = [c for c in new_comments if c["author"] in self.config.users]
                if new_comments:
                    for comment in new_comments:
                        await self.db.create_event(issue.id, "new_comment", comment)
                    max_id = max(c["id"] for c in new_comments)
                    await self.db.update_last_issue_comment_id(issue.id, max_id)
                    logger.info("New issue comments on %s/%s#%d: %d", owner, name, issue.issue_number, len(new_comments))
                continue  # Skip the PR comment polling

            if not issue.pr_number:
                continue

            # 3a. Issue comments (existing)
            try:
                comments = await self.github.get_pr_comments(owner, name, issue.pr_number)
            except Exception:
                logger.exception("Error fetching comments for PR #%d", issue.pr_number)
                continue

            new_comments = [c for c in comments if c["id"] > issue.last_comment_id]
            new_comments = [c for c in new_comments if c["author"] in self.config.users]

            if new_comments:
                await self.db.create_comment_events(issue.id, new_comments)
                logger.info("New comments on %s/%s PR#%d: %d",
                           owner, name, issue.pr_number, len(new_comments))

            # 3b. PR reviews
            try:
                reviews = await self.github.get_pr_reviews(owner, name, issue.pr_number)
                review_comments = await self.github.get_pr_review_comments(owner, name, issue.pr_number)
            except Exception:
                logger.exception("Error fetching reviews for PR #%d", issue.pr_number)
                continue

            new_reviews = [r for r in reviews if r["id"] > issue.last_review_id]
            new_reviews = [r for r in new_reviews if r["author"] in self.config.users]
            new_reviews = [r for r in new_reviews if r["state"] != "DISMISSED"]

            if new_reviews:
                assembled = self._assemble_review_events(new_reviews, review_comments)
                await self.db.create_review_events(issue.id, assembled)
                logger.info("New reviews on %s/%s PR#%d: %d",
                           owner, name, issue.pr_number, len(assembled))

    async def _check_reopen(self, owner: str, name: str, issue):
        """Check if a closed-then-reopened issue has a new comment from an allowed user."""
        try:
            comments = await self.github.get_pr_comments(owner, name, issue.issue_number)
        except Exception:
            logger.exception("Error fetching issue comments for #%d", issue.issue_number)
            return

        new_comments = [c for c in comments if c["id"] > issue.last_issue_comment_id]
        new_comments = [c for c in new_comments if c["author"] in self.config.users]

        if new_comments:
            latest = max(new_comments, key=lambda c: c["id"])
            await self.db.update_last_issue_comment_id(issue.id, latest["id"])
            await self.db.create_event(issue.id, "reopen", latest)
            logger.info("Reopened issue: %s/%s#%d", owner, name, issue.issue_number)

    async def _snapshot_and_mark_closed(self, owner: str, name: str, issue):
        """Snapshot issue comment ID and mark issue as closed."""
        try:
            comments = await self.github.get_pr_comments(owner, name, issue.issue_number)
        except Exception:
            logger.exception("Error fetching issue comments for #%d on close detection", issue.issue_number)
            return  # Skip — will retry on next poll

        max_id = max((c["id"] for c in comments), default=0)
        await self.db.mark_issue_closed(issue.id, max_id)
        logger.info("Detected closure of %s/%s#%d", owner, name, issue.issue_number)

    def _assemble_review_events(self, reviews: list[dict], all_inline: list[dict]) -> list[dict]:
        """Bundle each review with its inline comments into a single event payload."""
        inline_by_review: dict[int, list[dict]] = {}
        for c in all_inline:
            rid = c.get("review_id")
            if rid is not None:
                inline_by_review.setdefault(rid, []).append(c)

        assembled = []
        for review in reviews:
            inline = inline_by_review.get(review["id"], [])
            body = self._format_review_body(review, inline)
            assembled.append({
                "id": review["id"],
                "body": body,
                "author": review["author"],
                "state": review["state"],
                "inline_comments": inline,
            })
        return assembled

    @staticmethod
    def _format_review_body(review: dict, inline_comments: list[dict]) -> str:
        """Format a review + inline comments into a single body string."""
        parts = []
        state = review.get("state", "COMMENTED")
        parts.append(f"[Review \u2014 {state}]")

        if review.get("body"):
            parts.append("")
            parts.append(review["body"])

        if inline_comments:
            parts.append("")
            parts.append("Inline comments:")
            for c in inline_comments:
                path = c.get("path", "unknown")
                line = c.get("line", "?")
                body = c.get("body", "")
                parts.append(f"- {path}:{line} \u2014 {body}")

        return "\n".join(parts)
