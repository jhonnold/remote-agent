# tests/test_workspace.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from remote_agent.workspace import WorkspaceManager
from remote_agent.config import WorkspaceConfig
from remote_agent.exceptions import GitError


@pytest.fixture
def mock_github():
    gh = AsyncMock()
    gh.clone_repo = AsyncMock()
    gh.detect_default_branch = AsyncMock(return_value="main")
    return gh


@pytest.fixture
def workspace_mgr(tmp_path, mock_github):
    config = MagicMock()
    config.workspace = WorkspaceConfig(base_dir=str(tmp_path))
    return WorkspaceManager(config, mock_github)


def test_workspace_path(workspace_mgr, tmp_path):
    path = workspace_mgr._workspace_path("owner", "repo", 42)
    assert path == tmp_path / "owner" / "repo" / "issue-42"


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_ensure_workspace_clones_new(mock_git, workspace_mgr, mock_github):
    mock_git.return_value = ""
    workspace = await workspace_mgr.ensure_workspace("owner", "repo", 42)
    mock_github.clone_repo.assert_called_once()
    assert "issue-42" in workspace


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_ensure_workspace_pulls_existing(mock_git, workspace_mgr, mock_github, tmp_path):
    # Pre-create workspace directory
    ws_path = tmp_path / "owner" / "repo" / "issue-42"
    ws_path.mkdir(parents=True)
    mock_git.return_value = ""
    workspace = await workspace_mgr.ensure_workspace("owner", "repo", 42)
    mock_github.clone_repo.assert_not_called()  # Should not clone


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_ensure_branch_creates_new(mock_git, workspace_mgr):
    mock_git.side_effect = [GitError("not found"), ""]  # checkout fails, then -b succeeds
    await workspace_mgr.ensure_branch("/tmp/ws", "agent/issue-42")
    assert mock_git.call_count == 2


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_commit_and_push_with_changes(mock_git, workspace_mgr):
    mock_git.side_effect = ["", "M file.py\n", "", ""]  # add, status, commit, push
    await workspace_mgr.commit_and_push("/tmp/ws", "branch", "message")
    assert mock_git.call_count == 4


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_commit_and_push_no_changes_skips_commit(mock_git, workspace_mgr):
    mock_git.side_effect = ["", "", ""]  # add, status (empty), push
    await workspace_mgr.commit_and_push("/tmp/ws", "branch", "message")
    assert mock_git.call_count == 3  # No commit call


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_ensure_branch_force_deletes_remote_and_creates(mock_git, workspace_mgr):
    mock_git.return_value = ""  # All git calls succeed
    await workspace_mgr.ensure_branch("/tmp/ws", "agent/issue-42", force=True)
    calls = [c[0][0] for c in mock_git.call_args_list]
    assert calls[0] == ["push", "origin", "--delete", "agent/issue-42"]
    assert calls[1] == ["checkout", "-B", "agent/issue-42"]


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_ensure_branch_force_ignores_missing_remote(mock_git, workspace_mgr):
    mock_git.side_effect = [GitError("remote branch not found"), ""]  # delete fails, checkout succeeds
    await workspace_mgr.ensure_branch("/tmp/ws", "agent/issue-42", force=True)
    assert mock_git.call_count == 2  # Still tried checkout -B


def test_cleanup(workspace_mgr, tmp_path):
    ws_path = tmp_path / "owner" / "repo" / "issue-42"
    ws_path.mkdir(parents=True)
    (ws_path / "file.txt").write_text("test")
    workspace_mgr.cleanup("owner", "repo", 42)
    assert not ws_path.exists()
