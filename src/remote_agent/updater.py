# src/remote_agent/updater.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from remote_agent.exceptions import RemoteAgentError

logger = logging.getLogger(__name__)


class AutoUpdater:
    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path(__file__).resolve().parent.parent.parent

    async def check_for_update(self) -> bool:
        """Fetch origin/main and compare against local HEAD."""
        await self._run_git(["fetch", "origin", "main"])
        local = (await self._run_git(["rev-parse", "HEAD"])).strip()
        remote = (await self._run_git(["rev-parse", "origin/main"])).strip()
        if local != remote:
            logger.info("Update available: %s -> %s", local[:8], remote[:8])
            return True
        logger.debug("No update available (HEAD: %s)", local[:8])
        return False

    async def pull_update(self) -> None:
        """Pull latest main and reinstall dependencies."""
        await self._run_git(["pull", "--ff-only", "origin", "main"])
        await self._run_cmd("pip", ["install", "-e", "."])
        logger.info("Update pulled and dependencies reinstalled")

    async def _run_git(self, args: list[str]) -> str:
        return await self._run_cmd("git", args)

    async def _run_cmd(self, cmd: str, args: list[str]) -> str:
        logger.debug("%s %s", cmd, " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.repo_dir,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RemoteAgentError(f"{cmd} {' '.join(args)} failed: {stderr.decode().strip()}")
        return stdout.decode()
