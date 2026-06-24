---
source_url: https://github.com/jhonnold/remote-agent/blob/main/docs/superpowers/specs/2026-03-26-github-agent-design.md
ingested: 2026-06-03
source_type: plan
author: Jay + Claude
synthesis: done
---

# GitHub Agent System Design — remote-agent

**Date:** 2026-03-26
**Repo:** `jhonnold/remote-agent`

The foundational system design for the remote-agent project — an autonomous Python agent that turns GitHub issues into implemented pull requests.

## Problem

Jay wants to delegate real implementation work to an AI agent through GitHub issues and PR comments. Open an issue, label it, and the agent plans the work, gets feedback, writes the code, and opens a PR — driven entirely by natural-language comments.

Design constraints:
- **One issue at a time, sequentially** across any number of repos — serialization eliminates locking and workspace contention
- **Polling, not webhooks** — avoids exposing an inbound endpoint
- **Sandboxed, trusted environment** — runs with `bypassPermissions` in an isolated sandbox
- **Minimal deps** — `claude-agent-sdk`, `aiosqlite`, `pyyaml`

## Architecture

A polling loop driving an event-sourced state machine over SQLite.

```
main loop (poll → dispatch → sleep)
  ├── Poller        → writes events to SQLite (new_issue, new_comment, reopen)
  ├── Dispatcher    → reads unprocessed events, budget-gates, routes to handler
  │     └── Phase Handlers (planning, plan_review, implementing, code_review)
  │            └── Agent Service ── sole importer of claude_agent_sdk.query()
  ├── GitHub Service  → thin wrapper over the `gh` CLI (never httpx/REST)
  └── Workspace Mgr   → per-issue git checkouts, branching, reset, cleanup
```

**Layering discipline:**
- `agent.py` is the only module importing `claude_agent_sdk`
- `github.py` owns API-level GitHub ops; `workspace.py` owns filesystem repo management — they never overlap
- Prompts are Python modules returning parameterized strings

## State machine (7 phases)

| Phase | Agent active? | Meaning |
|-------|---------------|---------|
| `new` | No | Issue detected, not yet processed |
| `planning` | **Yes** | Agent writing the plan document |
| `plan_review` | No | Draft PR open, awaiting human comment |
| `implementing` | **Yes** | Agent writing code per the approved plan |
| `code_review` | No | Published PR, awaiting human comment |
| `completed` | No | Human approved; work done |
| `error` | No | Failure; awaiting human retry |

The agent only runs in `planning` and `implementing`. No mid-implementation interrupts — humans must wait for `code_review` to redirect.

Key transitions:
- `new → planning` (dispatcher picks up new issue)
- `planning → plan_review` (plan committed, draft PR created)
- `plan_review → implementing` (approved) or `→ planning` (revise)
- `implementing → code_review` (code pushed, PR ready)
- `code_review → completed` (approve), `→ implementing` (revise), or `→ planning` (back-to-planning: PR reverted to draft, `plan_approved` reset, branch hard-reset to stored plan commit)
- `error → planning | implementing` depending on whether a plan was already approved

## Key design decisions

**`revision_requested` events** — when a passive review handler kicks off an active phase, it creates a new event rather than transitioning directly. This keeps the dispatcher as the single mover and preserves the audit trail.

**Per-phase model selection** — `opus` for planning (reasoning-heavy), `sonnet` for implementation/review, `haiku` for the implementation orchestrator (later revised to `sonnet`).

**Two-stage review via tool starvation** — the implementation orchestrator is given *no* Write/Edit tools, structurally forcing delegation to subagents. Guarantees spec-compliance review before code-quality review without relying on the model to remember.

**Comment intent via MCP tool** — `classify_comment` tool (`mcp__review__classify_comment`) returns structured `{intent, response}` JSON. Runs with `max_turns=1` and `max_budget_usd=0.50`. Intents: `approve`, `revise`, `question`, `back_to_planning`. **Defaults to `revise` when uncertain.**

**Budget gating** — dispatcher checks daily budget before any agent work. If exceeded: posts one notification (guarded by `budget_notified`), leaves event unprocessed for later retry, otherwise skips silently.

**Startup recovery** — on boot, issues stuck in active phases with no unprocessed events are transitioned to `error`. No attempt to resume mid-run work.

**Crash-safe polling** — event inserts and `last_comment_id` high-water-mark update happen in a single transaction.

## Data model (SQLite, 3 tables)

- **`issues`** — durable per-issue state: `phase`, `branch_name`, `pr_number`, `plan_approved`, `plan_commit_hash`, `last_comment_id`, `budget_notified`
- **`events`** — append-only audit log (never deleted, only marked `processed`)
- **`agent_runs`** — single source of truth for `session_id` (used for resume via `resume=session_id`) and cost/token accounting; `cost_usd` written on every completion

`plan_commit_hash` is recorded when planning completes and powers the `reset_to_plan_commit` back-to-planning flow. Error runs may undercount cost (known/accepted limitation).

## Components

- **Poller** — per-repo, sequential. Lists labeled issues, filters to allowlisted authors, creates issue records + events. For review-phase issues, fetches PR comments via the issues API (not formal review comments), filters `id > last_comment_id`, and **filters out the agent's own comments** to avoid self-triggering.
- **Dispatcher** — deliberately dumb router: `(current_phase, event_type) → handler`. Does not interpret comments. Catches handler exceptions → transitions to `error` + posts to GitHub. Events always marked processed after handling (except budget-blocked) to prevent infinite retry loops.
- **Phase handlers** — stateless `PhaseHandler` protocol returning `PhaseResult(next_phase, error_message)`. Planning writes `docs/plans/issue-{N}-plan.md`, commits, records the plan hash, and opens a draft PR; same handler handles revisions transparently.
- **Agent Service** — all SDK interaction via `_run_query()`: creates `agent_runs` record, drains `query()` async generator, records session/cost/tokens. Subagents follow the superpowers pattern: sequential execution, spec compliance checked before code quality, max 3 review iterations, escalate by raising an exception.
- **GitHub Service** — every method shells out through `_run_gh()`; `gh` CLI is the sole interface. Default branch detected dynamically and cached per repo.
- **Workspace Manager** — checks out each issue to `{base_dir}/{owner}/{repo}/issue-{N}/` for full isolation; makes `git add -A` safe and `reset_to_plan_commit`'s force-push acceptable (always the agent's own branch).

## Error handling

`RemoteAgentError` hierarchy (`GitHubError`, `GitError`, `AgentError`, `BudgetExceededError`) with five layers: SDK errors recorded and re-raised in `_run_query`; handler errors caught by dispatcher → `error` + GitHub post; errors within the error handler logged but not cascaded; poller errors isolated per-repo; fatal errors logged with process exit for the supervisor to restart.

## Relationship to later designs

The 2026-03-27 prompt-flow-alignment design makes these significant revisions:
1. **Human gate moves upstream** — `plan_review` phase deleted; `designing`/`design_review` pair added ahead of planning
2. **Implementation orchestrator: haiku → sonnet**
3. **Richer comment classification** — `interpret_comment` + `answer_question` responder
4. **Plan doc becomes transient** (off-tree temp file); design doc becomes the committed permanent record

What survives unchanged: the poller/events/dispatcher backbone, SQLite event-sourced state machine, per-issue workspace isolation, `gh`-CLI-only GitHub layer, budget gate, startup recovery, and the two-stage spec-then-quality review discipline.
