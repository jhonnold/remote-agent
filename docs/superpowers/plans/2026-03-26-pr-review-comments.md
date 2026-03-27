# PR Review Comment Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support PR review comments (inline code review + review submissions) alongside existing conversation comments.

**Architecture:** Add two new GitHub API fetch methods, a new `last_review_id` tracking column, and review-assembly logic in the poller. Downstream handlers remain unchanged — the poller formats review payloads into the same `body` field they already consume.

**Tech Stack:** Python 3.11+, asyncio, aiosqlite, gh CLI

---

### Task 1: Add `last_review_id` to Issue model and DB schema

**Files:**
- Modify: `src/remote_agent/models.py:7-24` (Issue dataclass)
- Modify: `src/remote_agent/db.py:13-33` (SCHEMA), `db.py:80-86` (initialize), `db.py:310-320` (_row_to_issue)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing test for `last_review_id` field on Issue**

In `tests/test_db.py`, add:

```python
async def test_issue_has_last_review_id(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    issue = await db.get_issue("o", "r", 1)
    assert issue.last_review_id == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_issue_has_last_review_id -v`
Expected: FAIL with `AttributeError: ... has no attribute 'last_review_id'`

- [ ] **Step 3: Add `last_review_id` to Issue dataclass**

In `src/remote_agent/models.py`, add after `last_comment_id`:

```python
last_review_id: int = 0
```

- [ ] **Step 4: Add column to DB schema and update row mapper**

In `src/remote_agent/db.py`, in the `SCHEMA` string, add after the `last_comment_id` line:

```sql
last_review_id INTEGER DEFAULT 0,
```

In `_row_to_issue`, add `last_review_id=row["last_review_id"]` to the constructor call.

- [ ] **Step 5: Add migration for existing databases**

In `src/remote_agent/db.py`, update the `initialize` classmethod to run a migration after `executescript(SCHEMA)`:

```python
# Migrate existing databases
try:
    await conn.execute("ALTER TABLE issues ADD COLUMN last_review_id INTEGER DEFAULT 0")
    await conn.commit()
except Exception:
    pass  # Column already exists
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_db.py::test_issue_has_last_review_id -v`
Expected: PASS

- [ ] **Step 7: Write test for `create_review_events` DB method**

In `tests/test_db.py`, add:

```python
async def test_create_review_events(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)
    reviews = [
        {"id": 500, "body": "Change X"},
        {"id": 501, "body": "Also fix Y"},
    ]
    await db.create_review_events(issue_id, reviews)
    events = await db.get_unprocessed_events()
    assert len(events) == 2
    assert all(e.event_type == "new_comment" for e in events)
    issue = await db.get_issue("o", "r", 1)
    assert issue.last_review_id == 501
```

- [ ] **Step 8: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_create_review_events -v`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'create_review_events'`

- [ ] **Step 9: Implement `create_review_events` in Database**

In `src/remote_agent/db.py`, add after `create_comment_events`:

```python
async def create_review_events(self, issue_id: int, reviews: list[dict]):
    """Create events for PR reviews in a single transaction with last_review_id update."""
    logger.debug("Creating %d review events for issue %d", len(reviews), issue_id)
    if not reviews:
        return
    await self._conn.execute("BEGIN")
    try:
        for review in reviews:
            await self._conn.execute(
                "INSERT INTO events (issue_id, event_type, payload) VALUES (?, ?, ?)",
                (issue_id, "new_comment", json.dumps(review)),
            )
        max_id = max(r["id"] for r in reviews)
        await self._conn.execute(
            "UPDATE issues SET last_review_id = ?, updated_at = datetime('now') WHERE id = ?",
            (max_id, issue_id),
        )
        await self._conn.commit()
    except Exception:
        await self._conn.rollback()
        raise
```

- [ ] **Step 10: Run test to verify it passes**

Run: `pytest tests/test_db.py::test_create_review_events -v`
Expected: PASS

- [ ] **Step 11: Run full test suite to check for regressions**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 12: Commit**

```bash
git add src/remote_agent/models.py src/remote_agent/db.py tests/test_db.py
git commit -m "feat: add last_review_id tracking to Issue model and DB schema"
```

---

### Task 2: Add GitHub API methods for PR reviews and review comments

**Files:**
- Modify: `src/remote_agent/github.py:51-64` (add new methods after `get_pr_comments`)
- Test: `tests/test_github.py`

- [ ] **Step 1: Write failing test for `get_pr_reviews`**

In `tests/test_github.py`, add:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_github.py::test_get_pr_reviews -v`
Expected: FAIL with `AttributeError: 'GitHubService' object has no attribute 'get_pr_reviews'`

- [ ] **Step 3: Implement `get_pr_reviews`**

In `src/remote_agent/github.py`, add after `get_pr_comments`:

```python
async def get_pr_reviews(self, owner: str, repo: str, pr_number: int) -> list[dict]:
    output = await self._run_gh([
        "api", f"repos/{owner}/{repo}/pulls/{pr_number}/reviews",
    ])
    raw_reviews = json.loads(output) if output.strip() else []
    return [
        {
            "id": r["id"],
            "body": r.get("body", ""),
            "state": r["state"],
            "author": r["user"]["login"],
            "submitted_at": r.get("submitted_at", ""),
        }
        for r in raw_reviews
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_github.py::test_get_pr_reviews -v`
Expected: PASS

- [ ] **Step 5: Write failing test for `get_pr_review_comments`**

In `tests/test_github.py`, add:

```python
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
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_github.py::test_get_pr_review_comments -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 7: Implement `get_pr_review_comments`**

In `src/remote_agent/github.py`, add after `get_pr_reviews`:

```python
async def get_pr_review_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]:
    output = await self._run_gh([
        "api", f"repos/{owner}/{repo}/pulls/{pr_number}/comments",
    ])
    raw_comments = json.loads(output) if output.strip() else []
    return [
        {
            "id": c["id"],
            "body": c.get("body", ""),
            "path": c.get("path", ""),
            "line": c.get("line") or c.get("original_line"),
            "author": c["user"]["login"],
            "review_id": c.get("pull_request_review_id"),
            "created_at": c.get("created_at", ""),
        }
        for c in raw_comments
    ]
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_github.py::test_get_pr_review_comments -v`
Expected: PASS

- [ ] **Step 9: Write test for `get_pr_reviews` filtering out DISMISSED**

This is not handled in `github.py` itself (filtering happens in the poller), but add a test confirming the state field is properly returned so the poller can filter:

```python
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
```

- [ ] **Step 10: Run test and verify it passes**

Run: `pytest tests/test_github.py::test_get_pr_reviews_returns_state -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add src/remote_agent/github.py tests/test_github.py
git commit -m "feat: add GitHub API methods for PR reviews and review comments"
```

---

### Task 3: Update poller to fetch and assemble review events

**Files:**
- Modify: `src/remote_agent/poller.py:44-61` (add review polling after issue comment polling)
- Modify: `tests/test_poller.py` (update fixture + add new tests)
- Modify: `tests/test_integration.py` (update `github` fixture for new methods)

**Important:** The modified poller will now call `get_pr_reviews` and `get_pr_review_comments` on every poll cycle for review-phase issues. Existing tests use `AsyncMock()` for `mock_github`, and calling an undefined async mock method returns a coroutine that resolves to an `AsyncMock` (not a list), which will cause `TypeError` when iterated. We must update the mock fixtures first.

- [ ] **Step 1: Update `mock_github` fixture in `tests/test_poller.py`**

In `tests/test_poller.py`, update the `mock_github` fixture to set default empty-list return values for the new methods:

```python
@pytest.fixture
def mock_github():
    gh = AsyncMock()
    gh.get_pr_reviews.return_value = []
    gh.get_pr_review_comments.return_value = []
    return gh
```

- [ ] **Step 2: Update `github` fixture in `tests/test_integration.py`**

In `tests/test_integration.py`, update the `github` fixture:

```python
@pytest.fixture
def github():
    gh = AsyncMock()
    gh.get_pr_reviews.return_value = []
    gh.get_pr_review_comments.return_value = []
    return gh
```

- [ ] **Step 3: Run existing tests to verify fixtures don't break anything**

Run: `pytest tests/test_poller.py tests/test_integration.py -v`
Expected: All existing tests still pass

- [ ] **Step 4: Commit fixture updates**

```bash
git add tests/test_poller.py tests/test_integration.py
git commit -m "test: update mock fixtures to include PR review method defaults"
```

- [ ] **Step 5: Write failing test for review comment detection**

In `tests/test_poller.py`, add:

```python
async def test_poll_detects_new_pr_reviews(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 500, "body": "Needs changes", "state": "CHANGES_REQUESTED",
         "author": "testuser", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = [
        {"id": 900, "body": "use X here", "path": "src/foo.js", "line": 42,
         "author": "testuser", "review_id": 500, "created_at": "2026-01-01"}
    ]

    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1
    body = comment_events[0].payload["body"]
    assert "Needs changes" in body
    assert "src/foo.js:42" in body
    assert "use X here" in body
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_poller.py::test_poll_detects_new_pr_reviews -v`
Expected: FAIL (poller doesn't call `get_pr_reviews` yet)

- [ ] **Step 7: Write failing test for review with only inline comments (no body)**

In `tests/test_poller.py`, add:

```python
async def test_poll_review_with_only_inline_comments(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 600, "body": "", "state": "COMMENTED",
         "author": "testuser", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = [
        {"id": 901, "body": "fix this", "path": "src/bar.js", "line": 10,
         "author": "testuser", "review_id": 600, "created_at": "2026-01-01"}
    ]

    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1
    body = comment_events[0].payload["body"]
    assert "src/bar.js:10" in body
    assert "fix this" in body
```

- [ ] **Step 8: Write failing test for DISMISSED review filtering**

In `tests/test_poller.py`, add:

```python
async def test_poll_filters_dismissed_reviews(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 700, "body": "old review", "state": "DISMISSED",
         "author": "testuser", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = []

    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 0
```

- [ ] **Step 9: Write failing test for non-allowlisted review author filtering**

In `tests/test_poller.py`, add:

```python
async def test_poll_filters_non_allowlisted_review_author(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 800, "body": "looks good", "state": "APPROVED",
         "author": "stranger", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = []

    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 0
```

- [ ] **Step 10: Run all new tests to confirm they fail**

Run: `pytest tests/test_poller.py -v -k "review"`
Expected: All new review tests FAIL

- [ ] **Step 11: Implement review polling in `_poll_repo`**

In `src/remote_agent/poller.py`, add a helper method and update `_poll_repo`. After the existing issue comment block (lines 44-61), add review polling:

```python
async def _poll_repo(self, owner: str, name: str):
    # 1. Check for new issues (existing code unchanged)
    ...

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
```

Add the assembly helper method to the `Poller` class:

```python
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
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `pytest tests/test_poller.py -v`
Expected: All tests pass (existing + new)

- [ ] **Step 13: Write test for `last_review_id` update after polling**

In `tests/test_poller.py`, add:

```python
async def test_poll_updates_last_review_id(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = []
    mock_github.get_pr_reviews.return_value = [
        {"id": 500, "body": "ok", "state": "COMMENTED",
         "author": "testuser", "submitted_at": "2026-01-01"}
    ]
    mock_github.get_pr_review_comments.return_value = []

    await poller.poll_once()
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.last_review_id == 500

    # Second poll should not re-create event
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1  # Still just 1
```

- [ ] **Step 14: Run test to verify it passes**

Run: `pytest tests/test_poller.py::test_poll_updates_last_review_id -v`
Expected: PASS

- [ ] **Step 15: Run full test suite**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 16: Commit**

```bash
git add src/remote_agent/poller.py tests/test_poller.py
git commit -m "feat: add PR review comment polling with inline comment assembly"
```

---

### Task 4: Add integration test for review comment lifecycle

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write integration test for review-based revision flow**

In `tests/test_integration.py`, add:

```python
async def test_review_comment_triggers_revision(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    """Test: plan_review -> user submits PR review with inline comments -> revision -> planning"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
    dispatcher._planning.agent_service = agent_service
    dispatcher._planning.workspace_mgr = workspace_mgr
    dispatcher._planning.github = github
    dispatcher._plan_review.agent_service = agent_service
    dispatcher._plan_review.github = github

    # Setup: create issue already in plan_review with a PR
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "abc123"
    agent_service.run_planning.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )
    github.create_pr.return_value = 5

    await poller.poll_once()
    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "plan_review"

    # User submits a PR review with inline comments (no issue comments)
    github.get_pr_comments.return_value = []
    github.get_pr_reviews.return_value = [
        {"id": 500, "body": "A few changes needed", "state": "CHANGES_REQUESTED",
         "author": "testuser", "submitted_at": "2026-01-02"}
    ]
    github.get_pr_review_comments.return_value = [
        {"id": 900, "body": "use a map here instead", "path": "src/app.js", "line": 15,
         "author": "testuser", "review_id": 500, "created_at": "2026-01-02"}
    ]

    await poller.poll_once()

    # Verify event was created with assembled body
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1
    payload = comment_events[0].payload
    assert "A few changes needed" in payload["body"]
    assert "src/app.js:15" in payload["body"]
    assert "use a map here instead" in payload["body"]

    # Dispatcher routes to plan_review handler, which classifies as revise
    agent_service.interpret_comment.return_value = CommentInterpretation(intent="revise")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "planning"  # Revision sent back to planning
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/test_integration.py::test_review_comment_triggers_revision -v`
Expected: PASS (relies on Task 1-3 being complete)

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration test for PR review comment revision flow"
```

---

### Task 5: Migrate live database and verify

**Files:** None (operational task)

- [ ] **Step 1: Verify migration works on real database**

Run: `python3 -c "
import asyncio
from remote_agent.db import Database
async def check():
    db = await Database.initialize('data/agent.db')
    issue = await db.get_issue('jhonnold', 'node-tlcv', 130)
    print(f'last_review_id: {issue.last_review_id}')
    await db.close()
asyncio.run(check())
"`

Expected: `last_review_id: 0` (column added by migration, default value)

- [ ] **Step 2: Verify the agent still starts cleanly**

Run: `timeout 5 python3 -m remote_agent.main 2>&1 || true`
Expected: Agent starts polling without errors (will timeout after 5s, that's expected)
