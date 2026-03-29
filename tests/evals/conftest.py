# tests/evals/conftest.py
"""Pytest configuration and fixtures for LLM-as-judge eval tests."""
from __future__ import annotations

import subprocess
from uuid import uuid4

import pytest

from remote_agent.agent import AgentService
from remote_agent.config import (
    Config,
    RepoConfig,
    PollingConfig,
    TriggerConfig,
    WorkspaceConfig,
    DatabaseConfig,
    AgentConfig,
    LoggingConfig,
)
from remote_agent.db import Database
from tests.evals.fixtures.sample_issues import CACHING_ISSUE


@pytest.fixture(autouse=True)
def _skip_unless_evals(request):
    if not request.config.getoption("--run-evals"):
        pytest.skip("Pass --run-evals to run eval tests")


@pytest.fixture
def eval_config():
    return Config(
        repos=[RepoConfig(owner="eval", name="eval")],
        users=["eval-user"],
        polling=PollingConfig(interval_seconds=60),
        trigger=TriggerConfig(label="agent"),
        workspace=WorkspaceConfig(base_dir="/tmp/eval"),
        database=DatabaseConfig(path=""),
        agent=AgentConfig(
            planning_model="sonnet",
            max_turns=50,
            max_budget_usd=2.0,
        ),
        logging=LoggingConfig(),
    )


@pytest.fixture
async def eval_db(tmp_path):
    db = await Database.initialize(str(tmp_path / "eval.db"))
    await db.create_issue("eval", "eval", {
        "number": CACHING_ISSUE["issue_number"],
        "title": CACHING_ISSUE["issue_title"],
        "body": CACHING_ISSUE["issue_body"],
    })
    yield db
    await db.close()


@pytest.fixture
async def eval_agent_service(eval_config, eval_db):
    return AgentService(eval_config, eval_db)


def _find_repo_root() -> str:
    """Walk up from this file to find the .git directory."""
    from pathlib import Path

    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".git").exists():
            return str(current)
        current = current.parent
    raise RuntimeError("Could not find repo root (.git directory)")


@pytest.fixture
async def eval_workspace(tmp_path):
    import asyncio

    worktree_path = str(tmp_path / "eval-worktree")
    repo_root = _find_repo_root()
    branch = f"eval-{uuid4().hex[:8]}"

    # Create worktree
    await asyncio.to_thread(
        subprocess.run,
        ["git", "worktree", "add", "-b", branch, worktree_path, "HEAD"],
        cwd=repo_root,
        check=True,
    )

    yield worktree_path

    # Cleanup
    await asyncio.to_thread(
        subprocess.run,
        ["git", "worktree", "remove", "--force", worktree_path],
        cwd=repo_root,
        check=True,
    )
    await asyncio.to_thread(
        subprocess.run,
        ["git", "branch", "-D", branch],
        cwd=repo_root,
        check=True,
    )
