# tests/test_github.py
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.github import GitHubService
from remote_agent.exceptions import GitHubError


@pytest.fixture
def github():
    return GitHubService()


def _make_process_mock(stdout: str = "", stderr: str = "", returncode: int = 0):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.returncode = returncode
    return proc


@patch("asyncio.create_subprocess_exec")
async def test_list_issues(mock_exec, github):
    issues = [{"number": 1, "title": "Test", "body": "Body", "author": {"login": "user1"}}]
    mock_exec.return_value = _make_process_mock(stdout=json.dumps(issues))
    result = await github.list_issues("owner", "repo", "agent")
    assert len(result) == 1
    assert result[0]["number"] == 1
    mock_exec.assert_called_once()
    call_args = mock_exec.call_args[0]
    assert "gh" == call_args[0]
    assert "--label" in call_args
    assert "agent" in call_args


@patch("asyncio.create_subprocess_exec")
async def test_list_issues_gh_failure_raises(mock_exec, github):
    mock_exec.return_value = _make_process_mock(stderr="not found", returncode=1)
    with pytest.raises(GitHubError):
        await github.list_issues("owner", "repo", "agent")


@patch("asyncio.create_subprocess_exec")
async def test_get_pr_comments(mock_exec, github):
    comments = [{"id": 100, "body": "LGTM", "user": {"login": "user1"}, "created_at": "2026-01-01"}]
    mock_exec.return_value = _make_process_mock(stdout=json.dumps(comments))
    result = await github.get_pr_comments("owner", "repo", 5)
    assert len(result) == 1
    assert result[0]["id"] == 100
    assert result[0]["author"] == "user1"


@patch("asyncio.create_subprocess_exec")
async def test_create_pr_returns_number(mock_exec, github):
    mock_exec.return_value = _make_process_mock(stdout="https://github.com/owner/repo/pull/42\n")
    pr_number = await github.create_pr("owner", "repo", "Title", "Body", "branch", draft=True)
    assert pr_number == 42
    call_args = mock_exec.call_args[0]
    assert "--draft" in call_args


@patch("asyncio.create_subprocess_exec")
async def test_post_comment(mock_exec, github):
    mock_exec.return_value = _make_process_mock()
    await github.post_comment("owner", "repo", 42, "Hello")
    call_args = mock_exec.call_args[0]
    assert "comment" in call_args
    assert "42" in call_args


@patch("asyncio.create_subprocess_exec")
async def test_detect_default_branch(mock_exec, github):
    mock_exec.return_value = _make_process_mock(stdout="main\n")
    branch = await github.detect_default_branch("owner", "repo")
    assert branch == "main"
    # Second call should use cache
    branch2 = await github.detect_default_branch("owner", "repo")
    assert branch2 == "main"
    assert mock_exec.call_count == 1  # Cached


@patch("asyncio.create_subprocess_exec")
async def test_get_pr_reviews(mock_exec, github):
    reviews = [
        {
            "id": 500,
            "body": "Needs changes",
            "state": "CHANGES_REQUESTED",
            "user": {"login": "user1"},
            "submitted_at": "2026-01-01T00:00:00Z",
        }
    ]
    mock_exec.return_value = _make_process_mock(stdout=json.dumps(reviews))
    result = await github.get_pr_reviews("owner", "repo", 5)
    assert len(result) == 1
    assert result[0]["id"] == 500
    assert result[0]["author"] == "user1"
    assert result[0]["state"] == "CHANGES_REQUESTED"
    assert result[0]["body"] == "Needs changes"


@patch("asyncio.create_subprocess_exec")
async def test_get_pr_review_comments(mock_exec, github):
    comments = [
        {
            "id": 900,
            "body": "use X here",
            "path": "src/foo.js",
            "line": 42,
            "user": {"login": "user1"},
            "pull_request_review_id": 500,
            "created_at": "2026-01-01T00:00:00Z",
        }
    ]
    mock_exec.return_value = _make_process_mock(stdout=json.dumps(comments))
    result = await github.get_pr_review_comments("owner", "repo", 5)
    assert len(result) == 1
    assert result[0]["id"] == 900
    assert result[0]["path"] == "src/foo.js"
    assert result[0]["line"] == 42
    assert result[0]["review_id"] == 500


@patch("asyncio.create_subprocess_exec")
async def test_get_pr_reviews_returns_state(mock_exec, github):
    reviews = [
        {"id": 1, "body": "", "state": "DISMISSED", "user": {"login": "u"}, "submitted_at": ""},
        {"id": 2, "body": "ok", "state": "APPROVED", "user": {"login": "u"}, "submitted_at": ""},
    ]
    mock_exec.return_value = _make_process_mock(stdout=json.dumps(reviews))
    result = await github.get_pr_reviews("owner", "repo", 5)
    assert len(result) == 2
    assert result[0]["state"] == "DISMISSED"
    assert result[1]["state"] == "APPROVED"
