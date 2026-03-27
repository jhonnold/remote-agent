# Move Planning Phase from Draft PR to Issue Comments

**Issue:** #2
**Goal:** Move all planning-phase user interaction from draft PR comments to issue comments, deferring PR creation until implementation is complete.
**Architecture:** The planning handler will stop creating draft PRs and instead post the plan as an issue comment. The poller will poll issue comments (not PR comments) for any issue without a `pr_number` (covers both `plan_review` and `error` phases pre-PR). The implementation handler will take over PR creation responsibility, creating a non-draft PR with the actual code changes.

## Tasks

### Task 1: Update poller to poll issue comments when no PR exists

**Files:**
- Modify: `src/remote_agent/poller.py`
- Test: `tests/test_poller.py`

**Steps:**

1. Write failing test: Add tests that verify issues **without** a `pr_number` get polled for new comments via the issue number. This covers both `plan_review` (no PR yet) and `error` (planning failed before PR creation).

```python
# tests/test_poller.py
async def test_plan_review_polls_issue_comments_without_pr(poller, db, mock_github):
    """Issues in plan_review with no PR should poll issue comments."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    # No pr_number set — this issue has no PR yet

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "LGTM", "author": "testuser", "created_at": "2026-01-01"}
    ]

    await poller.poll_once()

    # Should fetch comments using issue_number (1), not pr_number
    mock_github.get_pr_comments.assert_called_with("owner", "repo", 1)
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1


async def test_error_phase_polls_issue_comments_without_pr(poller, db, mock_github):
    """Issues in error phase with no PR should poll issue comments for retry."""
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "error")
    # No pr_number — error occurred during planning before PR creation

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "retry", "author": "testuser", "created_at": "2026-01-01"}
    ]

    await poller.poll_once()

    mock_github.get_pr_comments.assert_called_with("owner", "repo", 1)
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1
```

2. Implement: In `poller.py`, replace the `if not issue.pr_number: continue` guard in step 3 of `_poll_repo` with a branch that polls issue comments for any issue without a PR:

```python
# In _poll_repo, replace step 3's loop body:
for issue in review_issues:
    if not issue.pr_number:
        # No PR exists — poll issue comments directly
        try:
            comments = await self.github.get_pr_comments(owner, name, issue.issue_number)
        except Exception:
            logger.exception("Error fetching issue comments for #%d", issue.issue_number)
            continue
        new_comments = [c for c in comments if c["id"] > issue.last_comment_id]
        new_comments = [c for c in new_comments if c["author"] in self.config.users]
        if new_comments:
            await self.db.create_comment_events(issue.id, new_comments)
            logger.info("New issue comments on %s/%s#%d: %d",
                       owner, name, issue.issue_number, len(new_comments))
        continue

    # Has PR — existing PR comment + review polling (unchanged)
    # 3a. Issue comments (on the PR)
    try:
        comments = await self.github.get_pr_comments(owner, name, issue.pr_number)
    # ... rest unchanged ...
```

**Why generalize beyond `plan_review`**: If planning fails (→ `error` phase) before a PR is created, the user needs to comment on the issue to retry. The `get_issues_awaiting_comment` query already returns `error` phase issues. The only change is removing the `pr_number` guard and branching instead.

**No `db.py` change needed**: The `get_issues_awaiting_comment` query already includes `plan_review`, `code_review`, and `error` phases. Issues without `pr_number` were just being skipped by the poller.

3. Verify: `python -m pytest tests/test_poller.py -v`

---

### Task 2: Update planning handler to post plan on issue instead of creating draft PR

**Files:**
- Modify: `src/remote_agent/phases/planning.py`
- Test: `tests/test_phases/test_planning.py`

**Steps:**

1. Write failing test: Update existing tests and add a new test verifying that the planning handler posts the plan as an issue comment and does NOT create a draft PR.

```python
# tests/test_phases/test_planning.py
async def test_planning_posts_plan_to_issue_not_pr(handler, deps, new_issue, new_issue_event):
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "# Plan\nSome plan content")
        result = await handler.handle(new_issue, new_issue_event)

    assert result.next_phase == "plan_review"
    # Should NOT create a draft PR
    deps["github"].create_pr.assert_not_called()
    deps["db"].update_issue_pr.assert_not_called()
    # Should post plan as issue comment using issue_number (42)
    deps["github"].post_comment.assert_called_once()
    call_args = deps["github"].post_comment.call_args
    assert call_args[0][2] == 42  # issue_number, not pr_number
    assert "# Plan" in call_args[0][3]  # Plan content in comment body
```

2. Implement: In `planning.py`, remove the entire draft PR creation block (lines 63–76) and replace the `post_comment` call (lines 78–81) to post to the issue with plan content:

```python
# planning.py handle() — replace lines 63–81 with:

# Read the plan content to post as issue comment
plan_content = plan_path.read_text() if plan_path.exists() else "Plan file created."

comment_body = (
    "## Plan\n\n"
    f"{plan_content}\n\n"
    "---\n"
    "*Review the plan above and comment with feedback, or approve to start implementation.*"
)

await self.github.post_comment(
    issue.repo_owner, issue.repo_name, issue.issue_number,
    comment_body,
)
```

Remove the `pr_number = issue.pr_number` variable and the `if not pr_number:` / `create_pr` / `update_issue_pr` / audit block entirely. Keep workspace setup, branch creation, agent call, commit/push, and `plan_commit_hash` tracking unchanged.

3. Verify: `python -m pytest tests/test_phases/test_planning.py -v`

---

### Task 3: Update plan_review handler to interact via issue comments

**Files:**
- Modify: `src/remote_agent/phases/plan_review.py`
- Test: `tests/test_phases/test_plan_review.py`

**Steps:**

1. Write failing test: Update tests to verify comments are posted to the issue (using `issue_number`) instead of the PR.

```python
# tests/test_phases/test_plan_review.py — update review_issue fixture:
@pytest.fixture
def review_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="", phase="plan_review", pr_number=None)

# Add explicit assertion tests:
async def test_approve_posts_to_issue(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    result = await handler.handle(review_issue, event)

    assert result.next_phase == "implementing"
    deps["github"].post_comment.assert_called_once()
    assert deps["github"].post_comment.call_args[0][2] == 42  # issue_number


async def test_question_posts_response_to_issue(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Why X?"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(
        intent="question", response="Because Y.")

    result = await handler.handle(review_issue, event)

    assert result.next_phase == "plan_review"
    assert deps["github"].post_comment.call_args[0][2] == 42  # issue_number
```

2. Implement: In `plan_review.py`, change two `post_comment` calls from `issue.pr_number` to `issue.issue_number`:

   - Line 38: `issue.pr_number` → `issue.issue_number` (approve comment)
   - Line 55: `issue.pr_number` → `issue.issue_number` (question response)

That's it — two token-level changes.

3. Verify: `python -m pytest tests/test_phases/test_plan_review.py -v`

---

### Task 4: Move PR creation to implementation handler

**Files:**
- Modify: `src/remote_agent/phases/implementation.py`
- Test: `tests/test_phases/test_implementation.py`

**Steps:**

1. Write failing test: Verify that the implementation handler creates a new (non-draft) PR when `issue.pr_number` is `None`, and that it skips PR creation when one already exists.

```python
# tests/test_phases/test_implementation.py
async def test_implementation_creates_pr_when_none_exists(handler, deps):
    """First implementation creates a non-draft PR."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="implementing",
                  branch_name="agent/issue-42", pr_number=None)
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["github"].create_pr.return_value = 15

    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "## Plan")
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    deps["github"].create_pr.assert_called_once()
    # Verify not draft
    call_kwargs = deps["github"].create_pr.call_args
    assert call_kwargs.kwargs.get("draft") is False or "draft" not in call_kwargs.kwargs
    deps["db"].update_issue_pr.assert_called_once_with(1, 15)
    # mark_pr_ready should NOT be called when creating a new PR
    deps["github"].mark_pr_ready.assert_not_called()


async def test_implementation_reuses_existing_pr(handler, deps, impl_issue):
    """Revision cycle with existing PR should not create a new one."""
    event = Event(id=1, issue_id=1, event_type="revision_requested",
                  payload={"body": "Fix the tests"})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"

    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "## Plan")
        result = await handler.handle(impl_issue, event)

    assert result.next_phase == "code_review"
    deps["github"].create_pr.assert_not_called()
    deps["db"].update_issue_pr.assert_not_called()
```

2. Implement: Replace the `mark_pr_ready` call (line 52) in `implementation.py` with conditional PR creation:

```python
# implementation.py handle() — replace lines 52–57 with:
pr_number = issue.pr_number
if not pr_number:
    pr_number = await self.github.create_pr(
        issue.repo_owner, issue.repo_name,
        title=f"[Agent] {issue.title}",
        body=f"Closes #{issue.issue_number}\n\nImplementation of the approved plan.",
        branch=issue.branch_name, draft=False,
    )
    await self.db.update_issue_pr(issue.id, pr_number)
    if self.audit:
        await self.audit.log(
            "github_api", "create_pr", issue_id=issue.id,
            detail={"pr_number": pr_number}, success=True,
        )

await self.github.post_comment(
    issue.repo_owner, issue.repo_name, pr_number,
    "Implementation complete. Please review the code and comment with feedback.",
)
```

**Key detail**: When `pr_number` already exists (code_review `revise` cycle), we do NOT call `mark_pr_ready` because PRs are never created as drafts in the new flow. The PR is already in ready state from its initial creation. We just post an updated comment.

3. Verify: `python -m pytest tests/test_phases/test_implementation.py -v`

---

### Task 5: Update code_review back-to-planning to close PR instead of marking draft

**Files:**
- Modify: `src/remote_agent/phases/code_review.py`
- Test: `tests/test_phases/test_code_review.py`

**Steps:**

1. Write failing test: Verify that `back_to_planning` closes the existing PR and clears `pr_number` so a fresh one is created after re-implementation.

```python
# tests/test_phases/test_code_review.py — replace test_back_to_planning_resets_state:
async def test_back_to_planning_closes_pr_and_clears_state(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42",
                  plan_commit_hash="abc123", workspace_path="/tmp/ws")
    event = Event(id=1, issue_id=1, event_type="new_comment",
                  payload={"body": "back to planning"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(
        intent="back_to_planning")

    result = await handler.handle(issue, event)

    assert result.next_phase == "planning"
    deps["db"].set_plan_approved.assert_called_once_with(1, False)
    # Should close PR, not mark as draft
    deps["github"].close_pr.assert_called_once()
    deps["github"].mark_pr_draft.assert_not_called()
    # Should clear pr_number so a fresh PR is created later
    deps["db"].update_issue_pr.assert_called_once_with(1, None)
    deps["workspace_mgr"].reset_to_commit.assert_called_once()
    deps["db"].create_event.assert_called_once()
```

2. Implement: In `code_review.py`, replace the `back_to_planning` block (lines 53–61):

```python
elif interpretation.intent == "back_to_planning":
    await self.db.set_plan_approved(issue.id, False)
    await self.github.close_pr(
        issue.repo_owner, issue.repo_name, issue.pr_number,
        comment="Going back to planning. Will create a new PR after re-implementation.",
    )
    await self.db.update_issue_pr(issue.id, None)
    if issue.plan_commit_hash:
        await self.workspace_mgr.reset_to_commit(
            issue.workspace_path, issue.plan_commit_hash, issue.branch_name,
        )
    await self.db.create_event(issue.id, "revision_requested", event.payload)
    return PhaseResult(next_phase="planning")
```

Changes: `mark_pr_draft` → `close_pr` (with explanatory comment), add `update_issue_pr(issue.id, None)` to clear PR number.

3. Verify: `python -m pytest tests/test_phases/test_code_review.py -v`

---

### Task 6: Add `update_issue_pr` support for clearing PR number

**Files:**
- Modify: `src/remote_agent/db.py`
- Test: `tests/test_dispatcher.py`

**Steps:**

1. Write failing test: Verify `update_issue_pr` accepts `None` and the dispatcher posts error comments to the issue when no PR exists.

```python
# tests/test_dispatcher.py
async def test_error_comment_posted_to_issue_when_no_pr(mock_config, deps):
    """When an issue has no PR, error comments should go to the issue number."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="T", body="", phase="new", pr_number=None)
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    dispatcher = Dispatcher(mock_config, deps["db"], deps["github"],
                            deps["agent_service"], deps["workspace_mgr"])

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.side_effect = RuntimeError("boom")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    # Should post to issue_number (42) since pr_number is None
    deps["github"].post_comment.assert_called_once()
    assert deps["github"].post_comment.call_args[0][2] == 42
```

2. Implement: In `db.py`, update the `update_issue_pr` method signature (line 168) from `pr_number: int` to `pr_number: int | None`. Also update the debug log to handle None:

```python
async def update_issue_pr(self, issue_id: int, pr_number: int | None):
    await self._conn.execute(
        "UPDATE issues SET pr_number = ?, updated_at = datetime('now') WHERE id = ?",
        (pr_number, issue_id),
    )
    await self._conn.commit()
    logger.debug("Updated issue %d pr=%s", issue_id, pr_number)
```

**Dispatcher already correct**: The error handling (line 125) and budget notification (line 78) both use `target = issue.pr_number or issue.issue_number`, which correctly falls back to `issue_number` when `pr_number` is `None`. No dispatcher changes needed.

3. Verify: `python -m pytest tests/test_dispatcher.py -v`

---

### Task 7: Update existing tests to reflect new flow

**Files:**
- Modify: `tests/test_phases/test_planning.py`
- Modify: `tests/test_phases/test_plan_review.py`
- Modify: `tests/test_phases/test_implementation.py`
- Modify: `tests/test_phases/test_code_review.py`
- Modify: `tests/test_poller.py`
- Modify: `tests/test_integration.py`

**Steps:**

1. **`test_planning.py`**: Update `test_planning_creates_branch_and_pr`:
   - Rename to `test_planning_creates_branch_and_posts_to_issue`
   - Remove `deps["github"].create_pr.return_value = 10`
   - Assert `create_pr` NOT called, `update_issue_pr` NOT called
   - Assert `post_comment` called with `issue.issue_number` (42)
   - Keep branch/workspace/commit assertions unchanged

   Update `test_planning_revision_reuses_existing_pr`:
   - Rename to `test_planning_revision_posts_updated_plan_to_issue`
   - Remove PR-specific assertions
   - Assert plan is re-posted to issue

   Update `test_planning_audit_records`:
   - Remove `deps["github"].create_pr.return_value = 10`
   - Remove assertion for `"github_api"` in categories (no PR creation audit)

2. **`test_plan_review.py`**: Update `review_issue` fixture to `pr_number=None`. The existing `test_approve_transitions_to_implementing` test will need its `post_comment` assertion updated — it currently doesn't assert which number is used, but the `review_issue` fixture change ensures it tests the issue path.

3. **`test_implementation.py`**: Update `test_implementation_publishes_pr`:
   - `impl_issue` fixture already has `pr_number=10`, so this tests the "PR exists" path
   - Remove `deps["github"].mark_pr_ready.assert_called_once()` assertion (no longer called)
   - Assert `post_comment` is called with the existing PR number

4. **`test_code_review.py`**: Update `test_back_to_planning_resets_state` (replaced by Task 5's new test). Remove `mark_pr_draft` assertion, add `close_pr` and `update_issue_pr(1, None)` assertions.

5. **`test_poller.py`**: Update `test_poll_detects_new_pr_comments` — this test creates an issue in `plan_review` with `pr_number=10`. After this change, `plan_review` issues won't have a PR. Either:
   - Change it to `code_review` phase (which still uses PR comments), or
   - Change to test without PR (using issue comments path)

   The PR review tests (`test_poll_detects_new_pr_reviews`, etc.) should also be updated to use `code_review` phase since plan_review no longer uses PRs.

6. **`test_integration.py`**:
   - `test_full_lifecycle_happy_path`:
     - Step 2 (planning): Remove `github.create_pr.return_value = 5`. Assert `issue.pr_number is None` after planning.
     - Step 3 (plan approval): Change comment polling to use `issue.issue_number` (the mock setup `github.get_pr_comments.return_value` is already keyed to the mock's return, not a specific number — just verify it works). The key change: after plan_review the issue still has `pr_number=None`.
     - Step 4 (implementation): Add `github.create_pr.return_value = 5` here. Assert `issue.pr_number == 5` after implementation.
     - Step 5 (code approval): Unchanged — still polls PR comments.
   - `test_review_comment_triggers_revision`: Update similarly — planning shouldn't create a PR.
   - `test_completed_issue_reopen_lifecycle`: Remove `github.create_pr.return_value = 15` from planning step (plan doesn't create PR). The assertion `issue.pr_number == 15` should move to after implementation (if the test extends that far, otherwise remove).

7. Verify: `python -m pytest tests/ -v`

## Testing Strategy

1. Run unit tests for each changed phase handler individually.
2. Run the poller tests to verify issue comment polling works for `plan_review` and `error` phases without a PR.
3. Run the integration test to verify the full lifecycle: `new → planning → plan_review → implementing → code_review → completed`.
4. Run full suite: `python -m pytest tests/ -v`.
5. Manual verification: Create an issue with the `agent` label and confirm:
   - Plan appears as an issue comment (no draft PR created).
   - Commenting on the issue triggers plan review.
   - Approval triggers implementation which creates a real (non-draft) PR.

## Risks and Considerations

1. **Comment ID tracking across phases**: `last_comment_id` tracks issue comments during `plan_review` and PR comments during `code_review`. These phases are mutually exclusive, and GitHub comment IDs are globally monotonic (auto-incrementing across the entire GitHub instance), so PR comment IDs will always be higher than older issue comment IDs. The transition is safe because `create_comment_events` always updates `last_comment_id` to the max of the new batch.

2. **Agent's own comments**: When planning posts the plan as an issue comment, the poller's next poll will see it. The `c["author"] in self.config.users` filter prevents this from creating spurious events — the agent authenticates as a bot account that is not in the `users` allowlist. If the agent's GitHub identity were ever added to `config.users`, this would break.

3. **Plan comment length**: GitHub comments have a ~65,536 character limit. Large plans could exceed this. Acceptable for now; a future enhancement could truncate and link to the file.

4. **Back-to-planning from code_review**: Changes from `mark_pr_draft` to `close_pr` + clear `pr_number`. This means a new PR is created after re-implementation, which creates a cleaner history but loses PR-level conversation. This is the desired behavior per the issue.

5. **Reopen flow**: The existing reopen logic in the dispatcher already closes old PRs and clears state via `clear_issue_for_reopen`. This is compatible with the new flow since planning no longer creates PRs.

6. **Error phase without PR**: If planning fails before PR creation, the error comment goes to the issue (via `target = issue.pr_number or issue.issue_number` in the dispatcher). The poller's updated logic (Task 1) ensures "retry" comments on the issue are picked up. The dispatcher routes `new_comment` + `error` phase to `planning` (when `plan_approved` is false), which is correct.

7. **`gh issue comment` API compatibility**: The existing `post_comment` method uses `gh issue comment` which works identically for issues and PRs (GitHub treats PRs as issues). The existing `get_pr_comments` method uses `repos/{owner}/{repo}/issues/{number}/comments` which also works for both. No new GitHub API methods are needed.

8. **`mark_pr_ready` removal**: In the new flow, PRs are never created as drafts, so `mark_pr_ready` is never needed. The implementation handler's revision path (PR already exists from a previous implementation cycle) just posts a comment — the PR is already in ready state. The `mark_pr_ready` method stays in `github.py` for general use but is no longer called from any phase handler.
