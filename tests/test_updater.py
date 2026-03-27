# tests/test_updater.py
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from remote_agent.updater import AutoUpdater
from remote_agent.exceptions import RemoteAgentError


def _make_proc(stdout=b"", stderr=b"", returncode=0):
    proc = AsyncMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = returncode
    return proc


@patch("asyncio.create_subprocess_exec")
async def test_check_for_update_no_change(mock_exec):
    """Local and remote HEAD are the same — no update."""
    abc = b"abc123\n"
    mock_exec.side_effect = [
        _make_proc(),          # git fetch
        _make_proc(stdout=abc),  # git rev-parse HEAD
        _make_proc(stdout=abc),  # git rev-parse origin/main
    ]
    updater = AutoUpdater()
    assert await updater.check_for_update() is False


@patch("asyncio.create_subprocess_exec")
async def test_check_for_update_has_change(mock_exec):
    """Local and remote HEAD differ — update available."""
    mock_exec.side_effect = [
        _make_proc(),                       # git fetch
        _make_proc(stdout=b"abc123\n"),     # git rev-parse HEAD
        _make_proc(stdout=b"def456\n"),     # git rev-parse origin/main
    ]
    updater = AutoUpdater()
    assert await updater.check_for_update() is True


@patch("asyncio.create_subprocess_exec")
async def test_check_for_update_fetch_fails(mock_exec):
    """Fetch failure raises RemoteAgentError."""
    mock_exec.return_value = _make_proc(stderr=b"network error", returncode=1)
    updater = AutoUpdater()
    with pytest.raises(RemoteAgentError):
        await updater.check_for_update()


@patch("asyncio.create_subprocess_exec")
async def test_pull_update_success(mock_exec):
    """Pull + pip install both succeed."""
    mock_exec.side_effect = [
        _make_proc(),  # git pull
        _make_proc(),  # pip install
    ]
    updater = AutoUpdater()
    await updater.pull_update()
    assert mock_exec.call_count == 2
    # Verify git pull call
    args = mock_exec.call_args_list[0][0]
    assert args == ("git", "pull", "--ff-only", "origin", "main")
    # Verify pip install call
    args = mock_exec.call_args_list[1][0]
    assert args == ("pip", "install", "-e", ".")


@patch("asyncio.create_subprocess_exec")
async def test_pull_update_git_fails(mock_exec):
    """Pull failure raises RemoteAgentError."""
    mock_exec.return_value = _make_proc(stderr=b"merge conflict", returncode=1)
    updater = AutoUpdater()
    with pytest.raises(RemoteAgentError):
        await updater.pull_update()


@patch("asyncio.create_subprocess_exec")
async def test_pull_update_pip_fails(mock_exec):
    """Git pull succeeds but pip install fails — raises RemoteAgentError."""
    mock_exec.side_effect = [
        _make_proc(),  # git pull succeeds
        _make_proc(stderr=b"No matching distribution", returncode=1),  # pip fails
    ]
    updater = AutoUpdater()
    with pytest.raises(RemoteAgentError):
        await updater.pull_update()
