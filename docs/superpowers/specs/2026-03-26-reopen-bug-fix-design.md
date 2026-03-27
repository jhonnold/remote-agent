# Fix Issue Reopen Detection — Design Spec

**Date:** 2026-03-26
**Status:** Approved

## Problem

The poller treats any open GitHub issue with agent phase `completed` as "reopened," creating a `reopen` event on every poll cycle. This causes completed issues to regress to the planning phase immediately after the code review is approved — before the user has even merged the PR.

Root cause: `poller.py:39-41` conflates "issue is open on GitHub + phase is completed in DB" with "user reopened a closed issue."

## Requirements

1. Once a PR reaches `completed`, the agent never touches it again
2. A reopen requires all three conditions: the issue was closed on GitHub, then reopened, AND has a new comment from an allowed user
3. Reopened issues get a fresh PR (not reusing the old one)
4. Must handle the race condition where the agent sets phase=completed but the user hasn't closed the issue yet

## Design

### New Fields

Add two fields to the `Issue` model and `issues` DB table:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `issue_closed_seen` | bool | False | Set True when the poller detects the issue is no longer in the open issues list |
| `last_issue_comment_id` | int | 0 | Tracks the latest seen comment on the original issue (not the PR), snapshotted when the close is detected |

### Poller: Revised `_poll_repo`

The method is restructured into three steps:

**Step 1 (revised) — Process open issues:**

```
for each open issue with "agent" label:
  if not in DB:
    create issue + new_issue event                              # unchanged

  if completed/error AND issue_closed_seen=True:
    fetch comments on the ISSUE (not the PR) via /issues/{issue_number}/comments
    filter for comments with id > last_issue_comment_id from allowed users
    if new comment found:
      create reopen event (payload includes the new comment body)
      update last_issue_comment_id to the new comment's id

  if completed/error AND issue_closed_seen=False:
    skip                                                        # race condition avoidance
```

**Step 2 (new) — Detect closed issues:**

```
fetch all completed/error issues from DB for this repo
open_numbers = set of issue numbers from the open issues list
for each completed/error issue:
  if issue_number NOT in open_numbers AND issue_closed_seen=False:
    fetch comments on the issue, record max comment id
    atomically set issue_closed_seen=True AND last_issue_comment_id=max_id
    (via db.mark_issue_closed — both must succeed or neither)
```

This step only fires once per issue (when it transitions from open to closed). The API call to snapshot `last_issue_comment_id` happens at detection time, not on every poll.

**Step 3 (unchanged) — Poll PR comments/reviews** for issues in `plan_review`, `code_review`, or `error` phases.

### Dispatcher: Reopen Handling

When processing a `reopen` event, clear all stale state so the planning phase starts fresh:

```python
# In _process_event, before dispatching to handler:
if event.event_type == "reopen":
    await self.db.set_plan_approved(issue.id, False)          # existing
    await self.db.clear_issue_for_reopen(issue.id)            # new
```

`clear_issue_for_reopen` resets: `pr_number`, `branch_name`, `plan_commit_hash`, `workspace_path`, `last_comment_id`, `last_review_id`, `issue_closed_seen`.

The planning handler already creates a new PR when `pr_number` is None (`planning.py:62-75`), so no changes are needed there.

### Old PR Disposal

When a reopen fires, the old PR may still be open on GitHub (the issue was closed but the PR wasn't necessarily merged). The dispatcher's reopen handling should close the old PR before clearing state:

```python
if event.event_type == "reopen":
    # Close old PR if it exists
    if issue.pr_number:
        await self.github.close_pr(issue.repo_owner, issue.repo_name, issue.pr_number,
                                    comment="Issue reopened. Closing this PR in favor of a fresh one.")
    await self.db.set_plan_approved(issue.id, False)
    await self.db.clear_issue_for_reopen(issue.id)
```

This requires a new `close_pr` method on `GitHubService` that posts a comment and closes the PR via `gh pr close`.

### Branch Handling on Reopen

When the old PR was closed without merging, the branch `agent/issue-{number}` may still exist on the remote. After a fresh clone, `ensure_branch` would checkout the stale remote-tracking branch.

Fix: add a `force` parameter to `ensure_branch` that resets the branch to the current HEAD (default branch) and deletes any stale remote branch:

```python
async def ensure_branch(self, workspace: str, branch: str, *, force: bool = False) -> None:
    if force:
        # Delete stale remote branch if it exists (ignore errors if it doesn't)
        try:
            await self._run_git(["push", "origin", "--delete", branch], cwd=workspace)
        except GitError:
            pass
        await self._run_git(["checkout", "-B", branch], cwd=workspace)
        return
    # existing logic unchanged
```

The planning handler passes `force=True` when `issue.branch_name is None` (i.e., after a reopen cleared it). The remote branch deletion ensures the subsequent `commit_and_push` (which uses a regular `push -u`, not force-push) succeeds even if the old branch had diverged history. This is safe because the workspace was freshly cloned and there's no local work to preserve.

### GitHub Service

No new methods needed. `get_pr_comments` uses `/issues/{number}/comments` which works for both issues and PRs. The poller calls it with the issue number to fetch issue comments.

### DB Methods

New methods on `Database`:

- `mark_issue_closed(issue_id, last_issue_comment_id)` — atomically sets `issue_closed_seen=True` AND `last_issue_comment_id` in a single UPDATE. These two writes must be atomic: if `issue_closed_seen` is set without a correct `last_issue_comment_id`, all pre-existing comments would be treated as new on the next poll and trigger spurious reopens.
- `clear_issue_for_reopen(issue_id)` — nulls out `pr_number`, `branch_name`, `plan_commit_hash`, `workspace_path`, `last_comment_id`, `last_review_id`, `issue_closed_seen` in a single UPDATE
- `get_completed_or_error_issues(owner, name)` — returns issues with phase in `('completed', 'error')` for a given repo
- `update_last_issue_comment_id(issue_id, comment_id)` — updates the `last_issue_comment_id` field

### Schema Migration

Add columns with ALTER TABLE (same pattern as `last_review_id` migration in `Database.initialize`):

```python
await conn.execute("ALTER TABLE issues ADD COLUMN issue_closed_seen INTEGER DEFAULT 0")
await conn.execute("ALTER TABLE issues ADD COLUMN last_issue_comment_id INTEGER DEFAULT 0")
```

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Completed, issue still open (the PR #131 bug) | `issue_closed_seen=False` — skip. No regression. |
| Completed, issue closed, never reopened | `issue_closed_seen` set True. Issue stays off the open list. No action. |
| Completed, closed, reopened WITHOUT comment | No new comment found — no reopen event created. |
| Completed, closed, reopened WITH comment | All conditions met — reopen event — fresh planning — new PR. |
| Error state, issue still open | Same gating — `issue_closed_seen=False` — skip. Error recovery via PR comment polling (step 3) still works. |
| Second completion after reopen | `issue_closed_seen` was cleared on reopen, so False again. Same protection applies. |
| Old branch still on remote after reopen | `force=True` deletes stale remote branch, then creates local branch from default HEAD. |
| Old PR still open after reopen | Dispatcher closes old PR with explanatory comment before clearing state. |

## Files Changed

| File | Change |
|------|--------|
| `src/remote_agent/models.py` | Add `issue_closed_seen`, `last_issue_comment_id` fields |
| `src/remote_agent/db.py` | Schema migration, new methods, update `_row_to_issue` |
| `src/remote_agent/poller.py` | Restructure `_poll_repo` with three-step logic |
| `src/remote_agent/dispatcher.py` | Close old PR + `clear_issue_for_reopen` call on reopen events |
| `src/remote_agent/github.py` | Add `close_pr` method |
| `src/remote_agent/workspace.py` | Add `force` param to `ensure_branch` (deletes stale remote branch) |
| `src/remote_agent/phases/planning.py` | Pass `force=True` to `ensure_branch` when branch was cleared |
| `tests/test_poller.py` | New tests for reopen gating logic |
| `tests/test_dispatcher.py` | Update reopen event tests |
| `tests/test_integration.py` | Add reopen lifecycle test |
