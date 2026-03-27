# Fix Issue Reopen Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the poller so completed issues don't regress to planning, and genuine reopens (closed→open + new comment) create fresh PRs.

**Architecture:** Add `issue_closed_seen` and `last_issue_comment_id` fields to gate reopen detection. Restructure poller into 3 steps: process open issues, detect closed issues, poll PR comments. Update dispatcher to close old PR and clear state on reopen. Add `force` param to `ensure_branch` for stale remote branch cleanup.

**Tech Stack:** Python 3.11+, asyncio, aiosqlite, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-26-reopen-bug-fix-design.md`

---

### Task 1: Model + DB — New Fields and Methods

**Files:**
- Modify: `src/remote_agent/models.py:7-25`
- Modify: `src/remote_agent/db.py:13-73` (schema), `db.py:86-92` (migration), `db.py:339-349` (`_row_to_issue`)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for new DB methods**

Add to `tests/test_db.py`:

```python
async def test_issue_has_closed_seen_and_issue_comment_fields(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    issue = await db.get_issue("o", "r", 1)
    assert issue.issue_closed_seen is False
    assert issue.last_issue_comment_id == 0


async def test_mark_issue_closed(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.mark_issue_closed(issue_id, last_issue_comment_id=500)
    issue = await db.get_issue("o", "r", 1)
    assert issue.issue_closed_seen is True
    assert issue.last_issue_comment_id == 500


async def test_clear_issue_for_reopen(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.update_issue_pr(issue_id, 10)
    await db.update_issue_branch(issue_id, "agent/issue-1")
    await db.set_plan_commit_hash(issue_id, "abc123")
    await db.update_issue_workspace(issue_id, "/tmp/ws")
    await db.update_last_comment_id(issue_id, 100)
    await db.mark_issue_closed(issue_id, last_issue_comment_id=500)

    await db.clear_issue_for_reopen(issue_id)

    issue = await db.get_issue("o", "r", 1)
    assert issue.pr_number is None
    assert issue.branch_name is None
    assert issue.plan_commit_hash is None
    assert issue.workspace_path is None
    assert issue.last_comment_id == 0
    assert issue.last_review_id == 0
    assert issue.issue_closed_seen is False
    assert issue.last_issue_comment_id == 0


async def test_get_completed_or_error_issues(db):
    id1 = await db.create_issue("o", "r", {"number": 1, "title": "T1", "body": ""})
    id2 = await db.create_issue("o", "r", {"number": 2, "title": "T2", "body": ""})
    id3 = await db.create_issue("o", "r", {"number": 3, "title": "T3", "body": ""})
    id4 = await db.create_issue("o", "r", {"number": 4, "title": "T4", "body": ""})
    await db.update_issue_phase(id1, "completed")
    await db.update_issue_phase(id2, "error")
    await db.update_issue_phase(id3, "planning")
    await db.update_issue_phase(id4, "completed")
    issues = await db.get_completed_or_error_issues("o", "r")
    assert len(issues) == 3
    phases = {i.phase for i in issues}
    assert phases == {"completed", "error"}


async def test_update_last_issue_comment_id(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_last_issue_comment_id(issue_id, 999)
    issue = await db.get_issue("o", "r", 1)
    assert issue.last_issue_comment_id == 999
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "closed_seen or mark_issue_closed or clear_issue_for_reopen or completed_or_error or last_issue_comment"`
Expected: FAIL — fields/methods don't exist yet

- [ ] **Step 3: Add fields to Issue model**

In `src/remote_agent/models.py`, add to the `Issue` dataclass after `last_review_id`:

```python
    issue_closed_seen: bool = False
    last_issue_comment_id: int = 0
```

- [ ] **Step 4: Add schema columns and migration**

In `src/remote_agent/db.py`, update the `SCHEMA` string — add these columns to the `issues` CREATE TABLE after `last_review_id`:

```sql
    issue_closed_seen INTEGER DEFAULT 0,
    last_issue_comment_id INTEGER DEFAULT 0,
```

Add migration in `Database.initialize` after the existing `last_review_id` migration block:

```python
        try:
            await conn.execute("ALTER TABLE issues ADD COLUMN issue_closed_seen INTEGER DEFAULT 0")
            await conn.commit()
        except Exception:
            pass
        try:
            await conn.execute("ALTER TABLE issues ADD COLUMN last_issue_comment_id INTEGER DEFAULT 0")
            await conn.commit()
        except Exception:
            pass
```

- [ ] **Step 5: Update `_row_to_issue` mapper**

In `db.py`, update `_row_to_issue` to include the new fields:

```python
            issue_closed_seen=bool(row["issue_closed_seen"]),
            last_issue_comment_id=row["last_issue_comment_id"],
```

- [ ] **Step 6: Add new DB methods**

In `src/remote_agent/db.py`, add these methods to the `Database` class:

```python
    async def mark_issue_closed(self, issue_id: int, last_issue_comment_id: int):
        await self._conn.execute(
            "UPDATE issues SET issue_closed_seen = 1, last_issue_comment_id = ?, updated_at = datetime('now') WHERE id = ?",
            (last_issue_comment_id, issue_id),
        )
        await self._conn.commit()
        logger.debug("Marked issue %d as closed (last_issue_comment_id=%d)", issue_id, last_issue_comment_id)

    async def clear_issue_for_reopen(self, issue_id: int):
        await self._conn.execute(
            """UPDATE issues SET pr_number = NULL, branch_name = NULL, plan_commit_hash = NULL,
               workspace_path = NULL, last_comment_id = 0, last_review_id = 0,
               issue_closed_seen = 0, last_issue_comment_id = 0, updated_at = datetime('now')
               WHERE id = ?""",
            (issue_id,),
        )
        await self._conn.commit()
        logger.debug("Cleared issue %d for reopen", issue_id)

    async def get_completed_or_error_issues(self, repo_owner: str, repo_name: str) -> list[Issue]:
        cursor = await self._conn.execute(
            "SELECT * FROM issues WHERE repo_owner = ? AND repo_name = ? AND phase IN ('completed', 'error')",
            (repo_owner, repo_name),
        )
        rows = await cursor.fetchall()
        return [self._row_to_issue(r) for r in rows]

    async def update_last_issue_comment_id(self, issue_id: int, comment_id: int):
        await self._conn.execute(
            "UPDATE issues SET last_issue_comment_id = ?, updated_at = datetime('now') WHERE id = ?",
            (comment_id, issue_id),
        )
        await self._conn.commit()
        logger.debug("Updated issue %d last_issue_comment_id=%d", issue_id, comment_id)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add src/remote_agent/models.py src/remote_agent/db.py tests/test_db.py
git commit -m "feat: add issue_closed_seen and last_issue_comment_id fields with DB methods"
```

---

### Task 2: GitHub Service — Add `close_pr` Method

**Files:**
- Modify: `src/remote_agent/github.py:100-127`
- Test: `tests/test_github.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_github.py`:

```python
@patch("asyncio.create_subprocess_exec")
async def test_close_pr(mock_exec, github):
    mock_exec.return_value = _make_process_mock()
    await github.close_pr("owner", "repo", 42, comment="Closing old PR")
    assert mock_exec.call_count == 2  # comment + close
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_github.py::test_close_pr -v`
Expected: FAIL — `close_pr` not defined

- [ ] **Step 3: Implement `close_pr`**

Add to `GitHubService` in `src/remote_agent/github.py`, after `mark_pr_draft`:

```python
    async def close_pr(self, owner: str, repo: str, pr_number: int, comment: str | None = None) -> None:
        if comment:
            await self.post_comment(owner, repo, pr_number, comment)
        await self._run_gh(["pr", "close", str(pr_number), "--repo", f"{owner}/{repo}"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_github.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/github.py tests/test_github.py
git commit -m "feat: add close_pr method to GitHubService"
```

---

### Task 3: Workspace — Add `force` Param to `ensure_branch`

**Files:**
- Modify: `src/remote_agent/workspace.py:40-46`
- Test: `tests/test_workspace.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_workspace.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_workspace.py -v -k "force"`
Expected: FAIL — `force` param not accepted

- [ ] **Step 3: Implement force param**

Replace the `ensure_branch` method in `src/remote_agent/workspace.py`:

```python
    async def ensure_branch(self, workspace: str, branch: str, *, force: bool = False) -> None:
        if force:
            try:
                await self._run_git(["push", "origin", "--delete", branch], cwd=workspace)
            except GitError:
                pass
            await self._run_git(["checkout", "-B", branch], cwd=workspace)
            logger.info("Force-created branch %s", branch)
            return
        try:
            await self._run_git(["checkout", branch], cwd=workspace)
            await self._run_git(["pull", "origin", branch], cwd=workspace)
        except GitError:
            await self._run_git(["checkout", "-b", branch], cwd=workspace)
            logger.info("Created branch %s", branch)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_workspace.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/workspace.py tests/test_workspace.py
git commit -m "feat: add force param to ensure_branch for stale remote cleanup"
```

---

### Task 4: Poller — Restructure `_poll_repo`

This is the core bug fix. The poller's `_poll_repo` is restructured into 3 steps.

**Files:**
- Modify: `src/remote_agent/poller.py:25-80`
- Test: `tests/test_poller.py`

- [ ] **Step 1: Write failing tests for reopen gating**

First, update the `mock_github` fixture to add a safe default for `get_pr_comments`:

```python
@pytest.fixture
def mock_github():
    gh = AsyncMock()
    gh.get_pr_comments.return_value = []
    gh.get_pr_reviews.return_value = []
    gh.get_pr_review_comments.return_value = []
    return gh
```

Then add the new tests to `tests/test_poller.py`:

```python
async def test_poll_skips_completed_issue_not_closed(poller, db, mock_github):
    """Completed issue still open on GitHub should NOT create reopen event (the PR #131 bug)."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    # issue_closed_seen defaults to False

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 0  # No reopen event!


async def test_poll_detects_issue_closure(poller, db, mock_github):
    """When a completed issue disappears from open list, mark it as closed."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")

    mock_github.list_issues.return_value = []  # Issue is no longer open
    mock_github.get_pr_comments.return_value = [
        {"id": 50, "body": "old comment", "author": "testuser", "created_at": "2026-01-01"},
    ]
    await poller.poll_once()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.issue_closed_seen is True
    assert issue.last_issue_comment_id == 50


async def test_poll_creates_reopen_event_after_close_and_new_comment(poller, db, mock_github):
    """Genuine reopen: closed issue reappears with new comment."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.mark_issue_closed(issue_id, last_issue_comment_id=50)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 50, "body": "old comment", "author": "testuser", "created_at": "2026-01-01"},
        {"id": 100, "body": "Please reopen this", "author": "testuser", "created_at": "2026-01-02"},
    ]
    await poller.poll_once()

    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0].event_type == "reopen"


async def test_poll_no_reopen_without_new_comment(poller, db, mock_github):
    """Closed issue reopened but no new comment — no reopen event."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.mark_issue_closed(issue_id, last_issue_comment_id=50)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 50, "body": "old comment", "author": "testuser", "created_at": "2026-01-01"},
    ]
    await poller.poll_once()

    events = await db.get_unprocessed_events()
    assert len(events) == 0


async def test_poll_reopen_filters_non_allowlisted_comments(poller, db, mock_github):
    """New comment from non-allowlisted user should not trigger reopen."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.mark_issue_closed(issue_id, last_issue_comment_id=50)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "random comment", "author": "stranger", "created_at": "2026-01-02"},
    ]
    await poller.poll_once()

    events = await db.get_unprocessed_events()
    assert len(events) == 0


async def test_poll_skips_error_issue_not_closed(poller, db, mock_github):
    """Error issue still open on GitHub should NOT create reopen event."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "error")

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_poller.py -v -k "completed or closure or reopen or error_issue_not_closed"`
Expected: FAIL — old poller logic still creates unconditional reopen events

- [ ] **Step 3: Implement the restructured `_poll_repo`**

Replace `_poll_repo` in `src/remote_agent/poller.py`:

```python
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

        # 3. Check for new PR comments on issues in review or error phases
        review_issues = await self.db.get_issues_awaiting_comment(owner, name)
        for issue in review_issues:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_poller.py -v`
Expected: ALL PASS (including all existing tests)

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/poller.py tests/test_poller.py
git commit -m "fix: gate reopen detection on issue_closed_seen + new comment"
```

---

### Task 5: Dispatcher — Close Old PR and Clear State on Reopen

**Files:**
- Modify: `src/remote_agent/dispatcher.py:88-91`
- Test: `tests/test_dispatcher.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_dispatcher.py`:

```python
async def test_reopen_closes_old_pr_and_clears_state(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="completed", pr_number=5,
                  branch_name="agent/issue-1", plan_commit_hash="abc123",
                  issue_closed_seen=True)
    event = Event(id=1, issue_id=1, event_type="reopen",
                  payload={"body": "Please redo this"})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.return_value = PhaseResult(next_phase="plan_review")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    deps["github"].close_pr.assert_called_once_with("o", "r", 5,
        comment="Issue reopened. Closing this PR in favor of a fresh one.")
    deps["db"].set_plan_approved.assert_called_once_with(1, False)
    deps["db"].clear_issue_for_reopen.assert_called_once_with(1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dispatcher.py::test_reopen_closes_old_pr_and_clears_state -v`
Expected: FAIL — `close_pr` not called, `clear_issue_for_reopen` not called

- [ ] **Step 3: Implement updated reopen handling**

In `src/remote_agent/dispatcher.py`, replace the reopen handling block (lines 89-91):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dispatcher.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: close old PR and clear state on reopen events"
```

---

### Task 6: Planning Handler — Pass `force=True` for Fresh Branches

**Files:**
- Modify: `src/remote_agent/phases/planning.py:32-34`
- Test: `tests/test_phases/test_planning.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_phases/test_planning.py`:

```python
async def test_planning_after_reopen_uses_force_branch(handler, deps):
    """After reopen, branch_name is None — ensure_branch should use force=True."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="planning",
                  branch_name=None)  # Cleared by reopen
    event = Event(id=3, issue_id=1, event_type="new_issue",
                  payload={"number": 42, "title": "Add auth", "body": "Need OAuth2"})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )
    deps["github"].create_pr.return_value = 20

    result = await handler.handle(issue, event)

    assert result.next_phase == "plan_review"
    deps["workspace_mgr"].ensure_branch.assert_called_once_with("/tmp/ws", "agent/issue-42", force=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phases/test_planning.py::test_planning_after_reopen_uses_force_branch -v`
Expected: FAIL — `ensure_branch` called without `force=True`

- [ ] **Step 3: Implement force param in planning handler**

In `src/remote_agent/phases/planning.py`, replace the branch setup block (lines 32-34):

```python
        branch = issue.branch_name or f"agent/issue-{issue.issue_number}"
        force = issue.branch_name is None
        await self.workspace_mgr.ensure_branch(workspace, branch, force=force)
        await self.db.update_issue_branch(issue.id, branch)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_phases/test_planning.py -v`
Expected: ALL PASS

- [ ] **Step 5: Update existing `test_planning_creates_branch_and_pr` assertion**

The existing test uses `branch_name=None` (default), so after this change it will use `force=True`. Update the assertion from `ensure_branch.assert_called_once()` to match the new behavior:

In `tests/test_phases/test_planning.py`, in `test_planning_creates_branch_and_pr`, replace:

```python
    deps["workspace_mgr"].ensure_branch.assert_called_once()
```

with:

```python
    deps["workspace_mgr"].ensure_branch.assert_called_once_with("/tmp/ws", "agent/issue-42", force=True)
```

- [ ] **Step 6: Run all planning tests**

Run: `pytest tests/test_phases/test_planning.py -v`
Expected: ALL PASS (including `test_planning_revision_reuses_existing_pr` which uses `branch_name="agent/issue-42"` → `force=False`)

- [ ] **Step 7: Commit**

```bash
git add src/remote_agent/phases/planning.py tests/test_phases/test_planning.py
git commit -m "feat: pass force=True to ensure_branch when branch is fresh"
```

---

### Task 7: Integration Test — Full Reopen Lifecycle

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write the integration test**

Add to `tests/test_integration.py`:

```python
async def test_completed_issue_reopen_lifecycle(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    """Test: completed issue -> still open (no reopen) -> closed -> reopened with comment -> fresh planning -> new PR"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
    dispatcher._planning.agent_service = agent_service
    dispatcher._planning.workspace_mgr = workspace_mgr
    dispatcher._planning.github = github

    # Setup: create a completed issue with an existing PR
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "Add feature", "body": "Details"})
    await db.update_issue_phase(issue_id, "completed")
    await db.update_issue_pr(issue_id, 5)
    await db.update_issue_branch(issue_id, "agent/issue-1")

    # Poll 1: Issue still open on GitHub — should NOT create reopen
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 0

    # Poll 2: Issue closed (not in open list) — should mark as closed
    github.list_issues.return_value = []
    github.get_pr_comments.return_value = [
        {"id": 50, "body": "thanks", "author": "testuser", "created_at": "2026-01-01"},
    ]
    await poller.poll_once()
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.issue_closed_seen is True
    assert issue.last_issue_comment_id == 50

    # Poll 3: Issue reopened with new comment — should create reopen event
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    github.get_pr_comments.return_value = [
        {"id": 50, "body": "thanks", "author": "testuser", "created_at": "2026-01-01"},
        {"id": 200, "body": "Actually, please also handle edge case X", "author": "testuser", "created_at": "2026-01-05"},
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0].event_type == "reopen"

    # Dispatcher processes reopen: closes old PR, clears state, runs planning
    github.close_pr = AsyncMock()
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "new123"
    agent_service.run_planning.return_value = AgentResult(
        success=True, session_id="s-reopen", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )
    github.create_pr.return_value = 15  # New PR number

    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    # Verify old PR was closed
    github.close_pr.assert_called_once_with("owner", "repo", 5,
        comment="Issue reopened. Closing this PR in favor of a fresh one.")

    # Verify fresh state: new PR, branch cleared and regenerated
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "plan_review"
    assert issue.pr_number == 15  # New PR
    assert issue.issue_closed_seen is False  # Reset

    # Verify force=True was used for branch
    workspace_mgr.ensure_branch.assert_called_with("/tmp/ws", "agent/issue-1", force=True)
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/test_integration.py::test_completed_issue_reopen_lifecycle -v`
Expected: PASS (all components wired together)

- [ ] **Step 3: Run the full test suite**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration test for issue reopen lifecycle"
```
