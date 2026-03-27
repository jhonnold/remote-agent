# PR Review Comment Support — Design Spec

## Problem

The remote agent only fetches conversation-style comments from `issues/{pr}/comments`. When a user leaves feedback as a PR review (inline code comments or review submissions), the agent never sees them. The system must support both comment styles.

## GitHub API Endpoints

Three separate, non-overlapping endpoints:

| Endpoint | Returns | ID Namespace | Tracking Field |
|---|---|---|---|
| `GET repos/{o}/{r}/issues/{pr}/comments` | Conversation comments | Issue comment IDs | `last_comment_id` (existing) |
| `GET repos/{o}/{r}/pulls/{pr}/reviews` | Review submissions (approve, request changes, comment) | Review IDs | `last_review_id` (new) |
| `GET repos/{o}/{r}/pulls/{pr}/comments` | All inline review comments across all reviews | Review comment IDs | Grouped by `pull_request_review_id` |

Key facts validated against live API:
- **No overlap** between endpoints — different ID namespaces, zero duplication risk
- **Review IDs are monotonically increasing** — safe for `id > last_review_id` filtering
- `pulls/{pr}/comments` returns ALL inline comments with `pull_request_review_id` — no need for per-review fetch (`N+1` avoided)
- `since` parameter is **ignored** on the reviews endpoint — must filter client-side
- **PENDING reviews** are invisible to other users' tokens (correct behavior)
- Review field names: `user.login` (not `author`), `submitted_at` (not `created_at`)

## Design

### Fetch Strategy (per poll cycle)

```
poll_once() per review issue:
  ├─ GET issues/{pr}/comments         → filter id > last_comment_id      → new_comment events
  ├─ GET pulls/{pr}/reviews           → filter id > last_review_id       → new_comment events
  └─ GET pulls/{pr}/comments          → group by pull_request_review_id  → attach to review events
```

### Review Event Assembly

For each new review (filtered, non-DISMISSED, from allowed user):
1. Fetch inline comments from the flat `pulls/{pr}/comments` response, filter by `pull_request_review_id`
2. Format a unified `body` that includes review summary + inline comments with file/line context
3. Create a `new_comment` event with this formatted body

If a review has no top-level body (common for `COMMENTED` state), the body is built entirely from inline comments.

### Formatted Body Example

```
[Review — CHANGES_REQUESTED]

A few things need changing.

Inline comments:
- src/foo.js:42 — use X here instead of Y
- src/bar.js:10 — this will break if null
```

### Downstream Compatibility

Both review events and issue comment events produce `new_comment` event type with a `body` field. Phase handlers (`plan_review.py`, `code_review.py`) consume `event.payload.get("body", "")` — no changes needed downstream. The poller owns the formatting.

### Schema Change

Add `last_review_id INTEGER DEFAULT 0` to `issues` table. Existing databases migrated via `ALTER TABLE`.

### Filters

- `review.id > issue.last_review_id`
- `review["user"]["login"] in config.users`
- `review["state"] != "DISMISSED"`
