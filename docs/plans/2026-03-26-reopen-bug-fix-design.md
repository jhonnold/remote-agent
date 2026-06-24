---
source_url: https://github.com/jhonnold/remote-agent/blob/main/docs/superpowers/specs/2026-03-26-reopen-bug-fix-design.md
ingested: 2026-06-03
source_type: plan
author: Jay + Claude
synthesis: done
---

# Fix Issue Reopen Detection — remote-agent

**Date:** 2026-03-26 | **Status:** Approved | **Author:** Jay + Claude

A targeted bug-fix design correcting the `reopen` detection logic in the remote-agent poller.
The foundational 2026-03-26 design gave the poller a `reopen` event type but never defined a
sound condition for emitting it; this design supplies one and threads the required state through
the DB, dispatcher, GitHub service, and workspace manager.

## Problem

`poller.py:39-41` treated "issue is open on GitHub **and** phase is `completed` in the DB" as
"the user reopened a closed issue." This fires every poll cycle for a completed issue that hasn't
been closed yet. Effect: a completed issue regresses back to planning the moment the poll fires —
before Jay has even reviewed the PR. Motivating real-world failure: PR #131.

## Requirements

1. Once `completed`, the agent never touches the issue again.
2. A genuine reopen requires all three: issue was closed on GitHub, then reopened, AND has a new
   comment from an allowed user.
3. A reopened issue gets a **fresh PR**, not the old one reused.
4. Handle the race where the agent sets `phase=completed` but the user hasn't closed yet.

## Key approach — the was-closed gate

Two new columns on `issues`:

- `issue_closed_seen` (bool, default False) — set True when the poller first observes the issue
  absent from the open-issues list.
- `last_issue_comment_id` (int, default 0) — max comment ID on the issue itself (not the PR),
  snapshotted at close-detection time.

A reopen is only possible once `issue_closed_seen=True`, and "new comment" is measured against
the comment id frozen at the moment of closing.

### Poller: three-step `_poll_repo`

1. **Process open issues (revised):** For `completed`/`error` issues, reopen check only runs when
   `issue_closed_seen=True` — fetches `/issues/{n}/comments`, filters for `id > last_issue_comment_id`
   from allowed users, emits `reopen` event on hit. When `issue_closed_seen=False`: skip (race-condition
   guard that kills the PR #131 regression).
2. **Detect closed issues (new):** For each `completed`/`error` issue absent from the open list with
   `issue_closed_seen=False`, atomically record max comment id and set `issue_closed_seen=True` via
   `db.mark_issue_closed`. Fires exactly once per close transition.
3. **Poll PR comments/reviews (unchanged):** `plan_review`, `code_review`, `error` phases.

### Atomicity requirement

`mark_issue_closed` must write `issue_closed_seen` and `last_issue_comment_id` in a **single UPDATE**.
Writing `issue_closed_seen=True` without the correct comment id would make every pre-existing comment
appear new on the next poll, triggering spurious reopens.

### Dispatcher reset on reopen

When a `reopen` event is processed, before handler dispatch:
1. Close the old PR if `issue.pr_number` is set (new `GitHubService.close_pr` — comment + `gh pr close`).
2. Call `set_plan_approved(issue.id, False)` and new `clear_issue_for_reopen(issue.id)`, which nulls
   `pr_number`, `branch_name`, `plan_commit_hash`, `workspace_path`, `last_comment_id`, `last_review_id`,
   and `issue_closed_seen` in one UPDATE.
3. The planning handler already creates a new PR when `pr_number is None`, so the fresh-PR requirement
   falls out for free.

### Stale-branch handling on reopen

A closed-but-unmerged PR may leave `agent/issue-{n}` on the remote. After a fresh clone, `ensure_branch`
would check out that stale remote-tracking branch. Fix: `force` parameter on `ensure_branch` deletes the
stale remote branch (ignoring errors if absent) then `checkout -B` from default HEAD. Planning passes
`force=True` when `branch_name is None`. Deleting the remote branch lets the subsequent normal `push -u`
succeed even if history diverged.

## Edge cases

| Scenario | `issue_closed_seen` | Outcome |
|----------|---------------------|---------|
| Completed, issue still open (PR #131 bug) | False | Skip — no regression |
| Completed, closed, never reopened | True | No action |
| Closed + reopened, no new comment | True | No event |
| Closed + reopened with new comment | True | `reopen` → fresh planning |
| Error state, issue open | False | Skip; PR-comment polling still works |
| Second completion after reopen | False (cleared) | Same gate applies again |

## Files changed

`models.py`, `db.py` (migration + `mark_issue_closed`, `clear_issue_for_reopen`,
`get_completed_or_error_issues`, `update_last_issue_comment_id`, `_row_to_issue`), `poller.py`
(three-step restructure), `dispatcher.py` (close-old-PR + clear-state), `github.py` (`close_pr`),
`workspace.py` (`force` on `ensure_branch`), `phases/planning.py` (pass `force=True`), plus
`test_poller.py`, `test_dispatcher.py`, `test_integration.py`.
