# src/remote_agent/github.py
from __future__ import annotations
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

from remote_agent.exceptions import GitHubError


class GitHubService:
    def __init__(self):
        self._default_branch_cache: dict[str, str] = {}

    async def _run_gh(self, args: list[str], cwd: str | None = None) -> str:
        # Mask sensitive args for logging
        masked = []
        skip_next = False
        for arg in args:
            if skip_next:
                masked.append(f"<{len(arg)} chars>")
                skip_next = False
            elif arg in ("--body", "--title"):
                masked.append(arg)
                skip_next = True
            else:
                masked.append(arg)
        logger.debug("gh %s", " ".join(masked))
        proc = await asyncio.create_subprocess_exec(
            "gh", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise GitHubError(f"gh {' '.join(args)} failed: {stderr.decode().strip()}")
        return stdout.decode()

    async def list_issues(self, owner: str, repo: str, label: str) -> list[dict]:
        output = await self._run_gh([
            "issue", "list",
            "--repo", f"{owner}/{repo}",
            "--label", label,
            "--state", "open",
            "--json", "number,title,body,author",
        ])
        return json.loads(output) if output.strip() else []

    async def get_pr_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        output = await self._run_gh([
            "api", f"repos/{owner}/{repo}/issues/{pr_number}/comments",
        ])
        raw_comments = json.loads(output) if output.strip() else []
        return [
            {
                "id": c["id"],
                "body": c["body"],
                "author": c["user"]["login"],
                "created_at": c["created_at"],
            }
            for c in raw_comments
        ]

    async def create_pr(self, owner: str, repo: str, title: str,
                         body: str, branch: str, draft: bool = False) -> int:
        args = [
            "pr", "create",
            "--repo", f"{owner}/{repo}",
            "--title", title,
            "--body", body,
            "--head", branch,
        ]
        if draft:
            args.append("--draft")
        output = await self._run_gh(args)
        pr_url = output.strip()
        return int(pr_url.rstrip("/").split("/")[-1])

    async def mark_pr_ready(self, owner: str, repo: str, pr_number: int) -> None:
        await self._run_gh(["pr", "ready", str(pr_number), "--repo", f"{owner}/{repo}"])

    async def mark_pr_draft(self, owner: str, repo: str, pr_number: int) -> None:
        await self._run_gh(["pr", "ready", str(pr_number), "--repo", f"{owner}/{repo}", "--undo"])

    async def post_comment(self, owner: str, repo: str, number: int, body: str) -> None:
        await self._run_gh([
            "issue", "comment", str(number),
            "--repo", f"{owner}/{repo}",
            "--body", body,
        ])

    async def clone_repo(self, owner: str, repo: str, path: str) -> None:
        await self._run_gh(["repo", "clone", f"{owner}/{repo}", path])

    async def detect_default_branch(self, owner: str, repo: str) -> str:
        key = f"{owner}/{repo}"
        if key not in self._default_branch_cache:
            output = await self._run_gh([
                "repo", "view", f"{owner}/{repo}",
                "--json", "defaultBranchRef",
                "--jq", ".defaultBranchRef.name",
            ])
            self._default_branch_cache[key] = output.strip()
        return self._default_branch_cache[key]
