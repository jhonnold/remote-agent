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
        # 1. Check for new issues
        issues = await self.github.list_issues(owner, name, self.config.trigger.label)
        for issue_data in issues:
            author = issue_data.get("author", {}).get("login", "")
            if author not in self.config.users:
                continue

            existing = await self.db.get_issue(owner, name, issue_data["number"])
            if not existing:
                issue_id = await self.db.create_issue(owner, name, issue_data)
                if issue_id:
                    await self.db.create_event(issue_id, "new_issue", issue_data)
                    logger.info("New issue detected: %s/%s#%d", owner, name, issue_data["number"])
            elif existing.phase in ("completed", "error"):
                await self.db.create_event(existing.id, "reopen", issue_data)
                logger.info("Reopened issue: %s/%s#%d", owner, name, issue_data["number"])

        # 2. Check for new PR comments on issues in review or error phases
        review_issues = await self.db.get_issues_awaiting_comment(owner, name)
        for issue in review_issues:
            if not issue.pr_number:
                continue

            # 2a. Issue comments (existing)
            try:
                comments = await self.github.get_pr_comments(owner, name, issue.pr_number)
            except Exception:
                logger.exception("Error fetching comments for PR #%d", issue.pr_number)
                continue  # Preserve existing behavior: skip this issue entirely on fetch error

            new_comments = [c for c in comments if c["id"] > issue.last_comment_id]
            new_comments = [c for c in new_comments if c["author"] in self.config.users]

            if new_comments:
                await self.db.create_comment_events(issue.id, new_comments)
                logger.info("New comments on %s/%s PR#%d: %d",
                           owner, name, issue.pr_number, len(new_comments))

            # 2b. PR reviews
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
