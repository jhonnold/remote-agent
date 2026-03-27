# Move Planning Phase from Draft PR to Issue Comments

**Issue:** #2
**Goal:** Move all planning-phase user interaction from draft PR comments to issue comments, deferring PR creation until implementation is complete.
**Architecture:** The planning handler will stop creating draft PRs and instead post the plan as an issue comment. The poller will poll issue comments (not PR comments) for issues in `plan_review` phase. The implementation handler will take over PR creation responsibility, creating a non-draft PR with the actual code changes.

## Tasks

### Task 1: Update poller to poll issue comments during plan_review

**Files:**
- Modify: `src/remote_agent/poller.py`
- Modify: `src/remote_agent/db.py`
- Test: `tests/test_poller.py`

**Steps:**

1. Write failing test: Add a test that verifies issues in `plan_review` phase **without** a `pr_number` still get polled for new comments via the issue number.

```python
# tests/test_poller.py
async def test_plan_review_polls_issue_comments_without_pr(poller, deps):
    """Issues in plan_review with no PR should poll issue comments."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="T", body="", phase="plan_review",
                  pr_number=None, last_comment_id=0)
    deps["db"].get_issues_awaiting_comment.return_value = [issue]
    deps["github"].get_pr_comments.return_value = [
        {"id": 100, "body": "LGTM", "author": "alice", "created_at": "2026-01-01T00:00:00Z"}
    ]

    await poller._poll_repo("o", "r")

    # Should fetch comments using issue_number, not pr_number
    deps["github"].get_pr_comments.assert_called_with("o", "r", 42)
    deps["db"].create_comment_events.assert_called_once()
```

2. Implement:
   - In `db.py`, update `get_issues_awaiting_comment` to also return `plan_review` issues without a `pr_number` (the current query already includes `plan_review`, but the poller skips them if `pr_number` is `None`).
   - In `poller.py`, split the comment polling in `_poll_repo` step 3 into two paths:
     - **plan_review without PR**: poll issue comments using `issue.issue_number`, track via `last_comment_id` on the issue row.
     - **plan_review/code_review/error with PR**: existing behavior (poll PR comments + reviews).
   - The key change is removing the `if not issue.pr_number: continue` guard and instead branching on whether to use `issue.issue_number` or `issue.pr_number`.

```python
# In _poll_repo, replace step 3:
for issue in review_issues:
    if issue.phase == "plan_review" and not issue.pr_number:
        # Poll issue comments directly
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

    if not issue.pr_number:
        continue
    # ... existing PR comment + review polling ...
```

3. Verify: `python -m pytest tests/test_poller.py -v`

---

### Task 2: Update planning handler to post plan on issue instead of creating draft PR

**Files:**
- Modify: `src/remote_agent/phases/planning.py`
- Modify: `src/remote_agent/github.py`
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

    # Mock reading the plan file content
    with patch("remote_agent.phases.planning.Path") as MockPath:
        plan_file = MagicMock()
        plan_file.exists.return_value = True
        plan_file.read_text.return_value = "# Plan\nSome plan content"
        MockPath.return_value.__truediv__ = lambda self, other: plan_file

        result = await handler.handle(new_issue, new_issue_event)

    assert result.next_phase == "plan_review"
    # Should NOT create a draft PR
    deps["github"].create_pr.assert_not_called()
    deps["db"].update_issue_pr.assert_not_called()
    # Should post plan as issue comment
    deps["github"].post_comment.assert_called_once()
    call_args = deps["github"].post_comment.call_args
    assert call_args[0][2] == 42  # issue_number, not pr_number
```

2. Implement:
   - Remove the draft PR creation block from `PlanningHandler.handle()`.
   - After committing the plan to the branch, read the plan file content and post it as a comment on the **issue** (using `issue.issue_number`).
   - Format the comment with a header indicating it's a plan for review.

```python
# In planning.py handle(), replace PR creation and comment with:
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

   - Remove the `if not pr_number` / `create_pr` / `update_issue_pr` block entirely.
   - Keep the branch creation, workspace setup, agent call, commit/push, and plan_commit_hash tracking — those are all still needed for eventual PR.

3. Verify: `python -m pytest tests/test_phases/test_planning.py -v`

---

### Task 3: Update plan_review handler to interact via issue comments

**Files:**
- Modify: `src/remote_agent/phases/plan_review.py`
- Test: `tests/test_phases/test_plan_review.py`

**Steps:**

1. Write failing test: Update tests to verify comments are posted to the issue (using `issue_number`) instead of the PR.

```python
# tests/test_phases/test_plan_review.py
async def test_approve_posts_to_issue(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="plan_review", pr_number=None)
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    result = await handler.handle(issue, event)

    assert result.next_phase == "implementing"
    # Comment should go to issue, not PR
    deps["github"].post_comment.assert_called_once()
    assert deps["github"].post_comment.call_args[0][2] == 42  # issue_number

async def test_question_posts_response_to_issue(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="plan_review", pr_number=None)
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Why X?"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(
        intent="question", response="Because Y.")

    result = await handler.handle(issue, event)

    assert result.next_phase == "plan_review"
    assert deps["github"].post_comment.call_args[0][2] == 42  # issue_number
```

2. Implement:
   - In `PlanReviewHandler.handle()`, change all `post_comment` calls to use `issue.issue_number` instead of `issue.pr_number`.
   - For the `approve` intent, change the approval message to indicate implementation is starting.

```python
# plan_review.py — change all post_comment calls:
# Before: issue.pr_number
# After:  issue.issue_number

if interpretation.intent == "approve":
    await self.db.set_plan_approved(issue.id, True)
    await self.github.post_comment(
        issue.repo_owner, issue.repo_name, issue.issue_number,
        "Plan approved. Starting implementation...",
    )
    await self.db.create_event(issue.id, "revision_requested", {})
    ...

elif interpretation.intent == "question":
    response = interpretation.response or "I'll look into that."
    await self.github.post_comment(
        issue.repo_owner, issue.repo_name, issue.issue_number, response,
    )
    ...
```

3. Verify: `python -m pytest tests/test_phases/test_plan_review.py -v`

---

### Task 4: Move PR creation to implementation handler

**Files:**
- Modify: `src/remote_agent/phases/implementation.py`
- Test: `tests/test_phases/test_implementation.py`

**Steps:**

1. Write failing test: Verify that the implementation handler creates a new (non-draft) PR when `issue.pr_number` is `None`.

```python
# tests/test_phases/test_implementation.py
async def test_implementation_creates_pr_when_none_exists(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="implementing",
                  branch_name="agent/issue-42", pr_number=None)
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["github"].create_pr.return_value = 15

    # Mock plan file
    with patch("remote_agent.phases.implementation.Path") as MockPath:
        plan_file = MagicMock()
        plan_file.exists.return_value = True
        plan_file.read_text.return_value = "# Plan"
        MockPath.return_value.__truediv__ = lambda self, other: plan_file

        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    deps["github"].create_pr.assert_called_once()
    # PR should NOT be draft
    assert deps["github"].create_pr.call_args.kwargs.get("draft", True) is False
    deps["db"].update_issue_pr.assert_called_once_with(1, 15)
```

2. Implement:
   - After committing code changes, create the PR if `issue.pr_number` is None.
   - Use a proper title like `[Agent] {issue.title}` (not "Plan for...").
   - The body should reference the issue: `Closes #{issue.issue_number}`.
   - Do NOT pass `draft=True` — this is a real code PR.
   - If the PR already exists (revision cycle), just call `mark_pr_ready` as before.
   - Store the new PR number in the DB.

```python
# In implementation.py handle(), after commit_and_push:
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
else:
    await self.github.mark_pr_ready(issue.repo_owner, issue.repo_name, pr_number)

await self.github.post_comment(
    issue.repo_owner, issue.repo_name, pr_number,
    "Implementation complete. Please review the code and comment with feedback.",
)
```

3. Verify: `python -m pytest tests/test_phases/test_implementation.py -v`

---

### Task 5: Update code_review back-to-planning to close PR instead of marking draft

**Files:**
- Modify: `src/remote_agent/phases/code_review.py`
- Test: `tests/test_phases/test_code_review.py`

**Steps:**

1. Write failing test: Verify that `back_to_planning` closes the existing PR and clears `pr_number` so a fresh one is created after re-implementation.

```python
# tests/test_phases/test_code_review.py
async def test_back_to_planning_closes_pr(handler, deps):
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
    deps["github"].close_pr.assert_called_once()
    deps["db"].update_issue_pr.assert_called_once_with(1, None)
```

2. Implement:
   - In `CodeReviewHandler.handle()`, for `back_to_planning` intent:
     - Close the PR (with a comment explaining re-planning).
     - Clear `pr_number` in DB so implementation will create a fresh PR later.
     - Replace `mark_pr_draft` with `close_pr` + `update_issue_pr(issue.id, None)`.
     - Keep the `reset_to_commit` and plan_approved reset.

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

3. Verify: `python -m pytest tests/test_phases/test_code_review.py -v`

---

### Task 6: Add `update_issue_pr` support for clearing PR number + update dispatcher error comments

**Files:**
- Modify: `src/remote_agent/db.py`
- Modify: `src/remote_agent/dispatcher.py`
- Test: `tests/test_dispatcher.py`

**Steps:**

1. Write failing test: Verify `update_issue_pr` accepts `None` to clear the PR number, and that the dispatcher posts error comments to the issue when no PR exists.

```python
# tests/test_dispatcher.py
async def test_error_comment_posted_to_issue_when_no_pr(dispatcher, deps):
    """When an issue has no PR, error comments should go to the issue number."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="T", body="", phase="planning", pr_number=None)
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    # Make handler raise
    dispatcher._planning.handle = AsyncMock(side_effect=RuntimeError("boom"))

    await dispatcher.process_events()

    # Should post to issue_number (42) since pr_number is None
    deps["github"].post_comment.assert_called_once()
    assert deps["github"].post_comment.call_args[0][2] == 42
```

2. Implement:
   - In `db.py`, update `update_issue_pr` signature to accept `int | None`:

```python
async def update_issue_pr(self, issue_id: int, pr_number: int | None):
    await self._conn.execute(
        "UPDATE issues SET pr_number = ?, updated_at = datetime('now') WHERE id = ?",
        (pr_number, issue_id),
    )
    await self._conn.commit()
```

   - In `dispatcher.py`, the error handling and budget notification already use `target = issue.pr_number or issue.issue_number` — verify this pattern is correct. It should work as-is since `pr_number` will be `None` during planning, falling back to `issue_number`.

3. Verify: `python -m pytest tests/test_dispatcher.py -v`

---

### Task 7: Update existing tests to reflect new flow

**Files:**
- Modify: `tests/test_phases/test_planning.py`
- Modify: `tests/test_phases/test_plan_review.py`
- Modify: `tests/test_integration.py`

**Steps:**

1. Update `test_planning_creates_branch_and_pr` — rename to `test_planning_creates_branch_and_posts_to_issue`. Assert `create_pr` is NOT called, assert `post_comment` IS called with `issue.issue_number`.

2. Update `test_planning_revision_reuses_existing_pr` — remove PR-specific assertions, verify plan is re-posted to issue.

3. Update `test_plan_review.py` `review_issue` fixture — set `pr_number=None` since planning no longer creates a PR.

4. Update `tests/test_integration.py` — adjust the full lifecycle test to reflect the new flow:
   - Planning posts to issue, no draft PR.
   - Plan review reads from issue comments.
   - Implementation creates the PR.
   - Code review still works on the PR.

5. Verify: `python -m pytest tests/ -v`

## Testing Strategy

1. Run unit tests for each changed phase handler individually.
2. Run the poller tests to verify issue comment polling works for plan_review.
3. Run the integration test to verify the full lifecycle: `new → planning → plan_review → implementing → code_review → completed`.
4. Run full suite: `python -m pytest tests/ -v`.
5. Manual verification: Create an issue with the `agent` label and confirm:
   - Plan appears as an issue comment (no draft PR created).
   - Commenting on the issue triggers plan review.
   - Approval triggers implementation which creates a real PR.

## Risks and Considerations

1. **Comment ID tracking**: During `plan_review`, we track `last_comment_id` on the issue model for issue comments. This is the same field used for PR comments in `code_review`. Since these phases are mutually exclusive, this should be safe — but the transition from `plan_review` to `code_review` (via `implementing`) means the `last_comment_id` will switch from issue comment IDs to PR comment IDs. This is fine because `create_comment_events` always updates `last_comment_id` to the max of the new batch.

2. **Plan comment length**: GitHub comments have a ~65,536 character limit. Large plans could hit this. For now this is acceptable; a future enhancement could truncate and link to the file.

3. **Back-to-planning from code_review**: Currently marks the PR as draft. The new behavior closes the PR entirely and clears `pr_number`. This means a new PR is created after re-implementation, which creates a cleaner history but loses PR-level conversation. This is the desired behavior per the issue.

4. **Reopen flow**: The existing reopen logic in the dispatcher already closes old PRs and clears state. This is compatible with the new flow since planning no longer creates PRs.

5. **Error comments**: The dispatcher's error handling already falls back to `issue.issue_number` when `pr_number` is None via `target = issue.pr_number or issue.issue_number`. No change needed.

6. **`gh issue comment` vs `gh api`**: The existing `post_comment` method uses `gh issue comment` which works for both issues and PRs (PRs are issues in GitHub's data model). The existing `get_pr_comments` method uses the issues API endpoint, so it also works for fetching issue comments. No new GitHub API methods are needed.
