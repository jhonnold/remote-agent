# src/remote_agent/workspace.py
from __future__ import annotations
import asyncio
import shutil
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

from remote_agent.config import Config
from remote_agent.exceptions import GitError
from remote_agent.github import GitHubService


class WorkspaceManager:
    def __init__(self, config: Config, github: GitHubService):
        self.base_dir = Path(config.workspace.base_dir)
        self.github = github

    def _workspace_path(self, owner: str, repo: str, issue_number: int) -> Path:
        return self.base_dir / owner / repo / f"issue-{issue_number}"

    async def ensure_workspace(self, owner: str, repo: str, issue_number: int) -> str:
        path = self._workspace_path(owner, repo, issue_number)
        if not path.exists():
            logger.info("Cloning %s/%s into %s", owner, repo, path)
            path.parent.mkdir(parents=True, exist_ok=True)
            await self.github.clone_repo(owner, repo, str(path))
            # Set git identity for agent commits
            await self._run_git(["config", "user.name", "Remote Agent"], cwd=str(path))
            await self._run_git(["config", "user.email", "agent@localhost"], cwd=str(path))
        else:
            logger.info("Updating workspace for %s/%s", owner, repo)
            default_branch = await self.github.detect_default_branch(owner, repo)
            await self._run_git(["fetch", "origin"], cwd=str(path))
            await self._run_git(["checkout", default_branch], cwd=str(path))
            await self._run_git(["pull"], cwd=str(path))
        return str(path)

    async def ensure_branch(self, workspace: str, branch: str) -> None:
        try:
            await self._run_git(["checkout", branch], cwd=workspace)
            await self._run_git(["pull", "origin", branch], cwd=workspace)
        except GitError:
            await self._run_git(["checkout", "-b", branch], cwd=workspace)
            logger.info("Created branch %s", branch)

    async def commit_and_push(self, workspace: str, branch: str, message: str) -> None:
        await self._run_git(["add", "-A"], cwd=workspace)
        status = await self._run_git(["status", "--porcelain"], cwd=workspace)
        if status.strip():
            await self._run_git(["commit", "-m", message], cwd=workspace)
        await self._run_git(["push", "-u", "origin", branch], cwd=workspace)
        logger.info("Pushed to branch %s", branch)

    async def get_head_commit(self, workspace: str) -> str:
        output = await self._run_git(["rev-parse", "HEAD"], cwd=workspace)
        return output.strip()

    async def reset_to_commit(self, workspace: str, commit_hash: str, branch: str) -> None:
        await self._run_git(["reset", "--hard", commit_hash], cwd=workspace)
        await self._run_git(["push", "--force", "origin", branch], cwd=workspace)

    def cleanup(self, owner: str, repo: str, issue_number: int) -> None:
        path = self._workspace_path(owner, repo, issue_number)
        if path.exists():
            logger.debug("Cleaned up workspace %s", path)
            shutil.rmtree(path)

    async def _run_git(self, args: list[str], cwd: str) -> str:
        logger.debug("git %s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
        return stdout.decode()
