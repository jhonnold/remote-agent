---
source_url: https://github.com/jhonnold/remote-agent/blob/main/docs/superpowers/specs/2026-03-26-pr-review-comments-design.md
ingested: 2026-06-03
source_type: plan
author: Jay + Claude
synthesis: done
migrated_from: library/imports/plans/2026-03-26-pr-review-comments-design.md
---

# PR Review Comment Support Design — remote-agent

**Date:** 2026-03-26
**Source:** `docs/superpowers/specs/2026-03-26-pr-review-comments-design.md` in `jhonnold/remote-agent`

Teaches remote-agent's poller to see PR reviews — inline code comments and review submissions (approve / request-changes / comment) — not just the conversation-style issue comments it already fetched. Extends the poller layer of the foundational `2026-03-26-github-agent-design` without touching the state machine or phase handlers downstream.

## Problem

The agent only fetched conversation comments from the GitHub `issues/{pr}/comments` endpoint. When Jay leaves feedback *as a PR review* — inline comments anchored to specific code lines, or a review submission with a verdict — the agent **never saw any of it**.

## GitHub API: three non-overlapping endpoints

| Endpoint | Returns | ID namespace | High-water field |
|---|---|---|---|
| `GET issues/{pr}/comments` | Conversation comments | issue comment IDs | `last_comment_id` (existing) |
| `GET pulls/{pr}/reviews` | Review submissions (approve / request-changes / comment) | review IDs | `last_review_id` (new) |
| `GET pulls/{pr}/comments` | All inline review comments across every review | review-comment IDs | grouped by `pull_request_review_id` |

Key API facts:

- **No overlap** between endpoints — different ID namespaces, no double-processing.
- **Review IDs are monotonically increasing** — `id > last_review_id` high-water filter is safe.
- `pulls/{pr}/comments` returns **all** inline comments in one flat list, each tagged with `pull_request_review_id` — single fetch, no N+1.
- `since` parameter is **ignored** on the reviews endpoint — filtering must happen client-side.
- **PENDING reviews are invisible** to other users' tokens (correct — agent shouldn't act on draft reviews).
- Review JSON uses `user.login` (not `author`) and `submitted_at` (not `created_at`).

## Design decisions

- **The poller owns all formatting; downstream is untouched.** Both review and issue-comment events emit the same `new_comment` event type with a `body` field. Phase handlers need no changes.
- **A second high-water mark.** Reviews tracked with `last_review_id`, filtered by `id > last_review_id` — mirrors the existing `last_comment_id` pattern exactly.
- **Single flat fetch for inline comments (N+1 avoided).** `pulls/{pr}/comments` carries `pull_request_review_id` on every comment; group in memory.
- **Client-side filtering is mandatory.** `since` is ignored on the reviews endpoint.
- **Reviews with no top-level body still produce an event.** Body built entirely from inline comments when review summary is empty.

## Fetch strategy

```
poll_once() per review issue:
  ├─ GET issues/{pr}/comments  → filter id > last_comment_id      → new_comment events
  ├─ GET pulls/{pr}/reviews    → filter id > last_review_id       → new_comment events
  └─ GET pulls/{pr}/comments   → group by pull_request_review_id  → attach to review events
```

## Review event assembly

For each new review:

1. Pull inline comments from the flat `pulls/{pr}/comments` response by matching `pull_request_review_id`.
2. Format a unified `body` combining review summary with inline comments (each carrying file/line context).
3. Emit a single `new_comment` event carrying that formatted body.

Body format:

```
[Review — CHANGES_REQUESTED]

A few things need changing.

Inline comments:
- src/foo.js:42 — use X here instead of Y
- src/bar.js:10 — this will break if null
```

## Filters per review

- `review.id > issue.last_review_id` — only unseen reviews.
- `review["user"]["login"] in config.users` — author allowlist (mirrors issue-comment discipline).
- `review["state"] != "DISMISSED"` — skip dismissed reviews.

## Schema change

Add `last_review_id INTEGER DEFAULT 0` to the `issues` table; existing databases migrated in place via `ALTER TABLE`. Only persistent-state change — additive, safe default.

## Relationship to other designs

- Extends the poller/events layer of the foundational `2026-03-26-github-agent-design`. That spec used the issues API only and filtered by `id > last_comment_id`; this design adds the second namespace and the parallel `last_review_id` high-water mark.
- Directly resolves the known limitation: formal PR review comments were invisible to the agent.
- Independent of `2026-03-27-remote-agent-prompt-flow-alignment-design` — that changes *comment classification*; this changes *what feedback the poller can retrieve*. They compose: richer classification is useful only once review feedback actually reaches the classifier.
