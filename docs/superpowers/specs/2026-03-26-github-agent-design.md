# GitHub Agent System Design

An autonomous Python agent that interacts with users via GitHub issues and PRs, using the Claude Agent SDK to plan and implement solutions.

## Overview

**Goal:** Build a polling-based agent that watches configured GitHub repositories for issues labeled `agent` from allowlisted users, creates detailed implementation plans as draft PRs, iterates on feedback via PR comments, implements code changes, and publishes PRs for review.

**Core technology:** Claude Agent SDK (`claude-agent-sdk` Python package) using `query()` for all agent interactions, with `bypassPermissions` mode in a sandboxed local environment.

**Processing model:** One issue at a time, sequentially, across multiple configured repositories.

## Project Structure

```
remote-agent/
├── config.yaml
├── pyproject.toml
├── src/
│   └── remote_agent/
│       ├── __init__.py
│       ├── main.py                # Entry point - starts poller loop
│       ├── config.py              # Config loading and validation (fail-fast)
│       ├── models.py              # Data models (Issue, Phase, Event, AgentResult)
│       ├── db.py                  # SQLite via aiosqlite - state persistence
│       ├── github.py              # GitHub service - thin wrapper over gh CLI
│       ├── workspace.py           # Workspace lifecycle - clone, branch, reset, cleanup
│       ├── agent.py               # Claude Agent SDK wrapper - query(), subagents, prompts
│       ├── poller.py              # Polls GitHub for issues/comments, queues events
│       ├── dispatcher.py          # Reads events, routes to correct phase handler
│       ├── phases/
│       │   ├── __init__.py
│       │   ├── base.py            # PhaseHandler protocol and PhaseResult
│       │   ├── planning.py        # Creates plan document
│       │   ├── plan_review.py     # Interprets comments on draft PR
│       │   ├── implementation.py  # Executes plan via subagents
│       │   └── code_review.py     # Interprets comments on published PR
│       └── prompts/
│           ├── __init__.py
│           ├── planning.py        # System prompt for planning phase
│           ├── implementation.py  # System prompt for implementation orchestrator
│           └── review.py          # System prompt for comment interpretation
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_poller.py
│   ├── test_dispatcher.py
│   ├── test_github.py
│   ├── test_workspace.py
│   ├── test_agent.py
│   ├── test_phases/
│   │   ├── test_planning.py
│   │   ├── test_plan_review.py
│   │   ├── test_implementation.py
│   │   └── test_code_review.py
│   └── test_integration.py
└── docs/
    └── superpowers/
        └── specs/
```

**Key decisions:**
- Prompts are Python modules with functions returning strings, enabling runtime parameterization (issue number, plan content, feedback).
- `agent.py` is the sole module that imports from `claude_agent_sdk`. All other modules interact through it.
- `github.py` handles API-level operations; `workspace.py` handles filesystem-level repo management.

## Configuration

```yaml
# config.yaml
repos:
  - owner: "myuser"
    name: "my-project"

users:
  - "myuser"
  - "trusted-collaborator"

polling:
  interval_seconds: 60

trigger:
  label: "agent"

workspace:
  base_dir: "/home/claude/workspaces"

database:
  path: "data/agent.db"   # Resolved relative to config file location

agent:
  default_model: "sonnet"
  planning_model: "opus"           # Strong reasoning for design work
  implementation_model: "sonnet"   # Cost-efficient for code generation
  review_model: "sonnet"           # Comment interpretation
  orchestrator_model: "haiku"      # Task decomposition and sequencing
  max_turns: 200                   # Per query() call
  max_budget_usd: 10.0             # Per query() call
  daily_budget_usd: 50.0           # Aggregate daily safety cap
```

**Key decisions:**
- Per-phase model selection optimizes cost/quality trade-offs.
- `orchestrator_model` uses haiku since the implementation orchestrator only decomposes and delegates.
- Database path resolved to absolute at config load time (relative to config file location).
- Flat user list, no per-repo overrides (YAGNI).
- Config validation fails fast on startup for missing/invalid values.
- The Agent SDK accepts model shorthand (`"sonnet"`, `"opus"`, `"haiku"`).

## State Machine

### Phases

| Phase | Description | Agent Active? |
|-------|-------------|---------------|
| `new` | Issue detected, not yet processed | No |
| `planning` | Agent creating plan document | Yes |
| `plan_review` | Draft PR open, waiting for human comment | No (waiting) |
| `implementing` | Agent writing code per plan | Yes |
| `code_review` | Published PR, waiting for human comment | No (waiting) |
| `completed` | Human approved, work done | No |
| `error` | Something went wrong, awaiting human retry | No |

### Transitions

| From | To | Trigger |
|------|-----|---------|
| `new` | `planning` | Dispatcher processes new issue event |
| `planning` | `plan_review` | Agent finishes plan, draft PR created |
| `planning` | `error` | Agent fails during planning |
| `plan_review` | `planning` | Human requests plan revision (creates `revision_requested` event) |
| `plan_review` | `implementing` | Human approves plan |
| `implementing` | `code_review` | Agent finishes code, PR published |
| `implementing` | `error` | Agent fails during implementation |
| `code_review` | `implementing` | Human requests code revision (creates `revision_requested` event) |
| `code_review` | `planning` | Human requests return to planning (creates `revision_requested` event, PR reverted to draft, plan_approved reset, branch reset to plan commit) |
| `code_review` | `completed` | Human approves code |
| `error` | `planning` | Human comments retry (no approved plan) |
| `error` | `implementing` | Human comments retry (plan previously approved) |

**Key decisions:**
- Only two "active" phases where the agent runs (`planning`, `implementing`). Review phases are passive.
- No mid-implementation interrupts. Human must wait for `code_review` to redirect.
- Review handlers create `revision_requested` events when triggering transitions to active phases, solving the "processed event, no driver" problem.
- Label removal: current phase runs to completion; issue is not picked up again.
- `error` is recoverable via human comment. Recovery path considers whether plan was previously approved.
- Startup recovery: on boot, issues in active phases with no unprocessed events transition to `error`.

## Database Schema

```sql
CREATE TABLE issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_owner TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    phase TEXT NOT NULL DEFAULT 'new',
    branch_name TEXT,
    pr_number INTEGER,
    workspace_path TEXT,
    plan_approved INTEGER DEFAULT 0,
    plan_commit_hash TEXT,              -- Recorded when planning completes; used for reset
    last_comment_id INTEGER DEFAULT 0,
    budget_notified INTEGER DEFAULT 0,  -- Prevents repeated budget-exceeded comments
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(repo_owner, repo_name, issue_number)
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    event_type TEXT NOT NULL,            -- 'new_issue', 'new_comment', 'revision_requested', 'reopen'
    payload TEXT NOT NULL DEFAULT '{}',  -- JSON
    processed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    phase TEXT NOT NULL,
    session_id TEXT,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    result TEXT,                          -- 'success', 'error', 'timeout'
    cost_usd REAL DEFAULT 0.0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    error_message TEXT
);
```

**Key decisions:**
- `session_id` lives only on `agent_runs` (single source of truth). On retry, look up the latest session for the phase.
- `plan_commit_hash` stored when planning completes, used by `reset_to_plan_commit` for back-to-planning flow.
- `budget_notified` prevents repeated budget-exceeded comments while leaving the event unprocessed.
- Events are never deleted (audit trail). Marked processed after handling.
- All event inserts + `last_comment_id` updates happen in a single transaction for crash safety.
- `cost_usd` written to `agent_runs` on every completion (success or error) for budget tracking. Error runs may undercount (known limitation).

## Poller Service

The poller runs on a configurable interval and checks all configured repos for new issues and PR comments.

### Poll Cycle

1. **For each configured repo:**
   - Fetch open issues with the trigger label via `gh issue list`
   - For each issue from an allowlisted user (checking `author.login`):
     - If not tracked: create issue record + `new_issue` event
     - If previously tracked but label was removed and re-added: create `reopen` event
   - For issues in review phases (`plan_review`, `code_review`):
     - Fetch PR comments via `gh api repos/{owner}/{repo}/issues/{pr_number}/comments`
     - Filter to `id > last_comment_id` and from allowlisted users only
     - Create `new_comment` events within a single transaction alongside `last_comment_id` update

### Key decisions
- Sequential polling across repos. No parallelism needed at personal project scale.
- Only polls PR comments (issue-style comments via the issues API), not GitHub review comments.
- Filters out the agent's own comments to prevent self-triggering.
- Transaction-safe: event creation + high-water mark update are atomic. If the process crashes mid-cycle, no events are lost.
- Polling pauses implicitly during long agent runs (sequential poll-then-dispatch). Acceptable trade-off for simplicity.

## Dispatcher

The dispatcher reads unprocessed events and routes them to phase handlers.

### Event Processing

```
For each unprocessed event:
  1. Load the associated issue
  2. Determine next phase from (current_phase, event_type)
  3. If active phase: check daily budget
     - Budget exceeded + not yet notified: post comment, set flag, leave event unprocessed
     - Budget exceeded + already notified: skip silently, leave event unprocessed
  4. Run the appropriate phase handler
  5. Update issue phase based on handler result
  6. Mark event as processed (always, even on error)
  7. On error: transition to 'error', post error to GitHub (with try/except on the post itself)
```

### Phase Routing

| Current Phase | Event Type | Action |
|---------------|------------|--------|
| `new` | `new_issue` | Run `planning` handler |
| `plan_review` | `new_comment` | Run `plan_review` handler |
| `code_review` | `new_comment` | Run `code_review` handler |
| `error` | `new_comment` | Interpret comment; if retry, route to `planning` or `implementing` |
| Any | `revision_requested` | Run the target phase handler (phase encoded in event payload) |
| Any | `reopen` | Run `planning` handler |

### Key decisions
- The dispatcher is a simple router. It does not interpret comments or make complex decisions.
- Budget check happens before any agent work. Events stay unprocessed when budget-blocked so they're retried when budget resets.
- Error handling is centralized. Phase handlers raise exceptions; the dispatcher catches, transitions, and notifies.
- Events are always marked processed after handling (except budget-blocked). Prevents infinite retry loops.
- Comprehensive logging at every dispatch: issue number, current phase, next phase, event type.

## Phase Handlers

All handlers implement a common protocol:

```python
@dataclass
class PhaseResult:
    next_phase: str          # Phase to transition to (return current phase = no transition)
    error_message: str | None = None

class PhaseHandler(Protocol):
    async def handle(self, issue: Issue, event: Event) -> PhaseResult: ...
```

### Planning Handler

1. Ensure workspace exists (clone or pull latest)
2. Create/checkout branch `agent/issue-{N}`
3. Build planning prompt with: issue title/body, existing plan (if revision), feedback comment (if revision)
4. Run agent via `agent_service.run_planning()` - agent writes plan to `docs/plans/issue-{N}-plan.md`
5. Commit plan file, record `plan_commit_hash`
6. Push branch
7. Create draft PR (if new) or push update (if revision)
8. Post comment on PR
9. Return `PhaseResult(next_phase="plan_review")`

### Plan Review Handler

1. Interpret the comment via `agent_service.interpret_comment()` (uses custom MCP tool for structured output)
2. Based on intent:
   - **approve**: set `plan_approved=True`, return `next_phase="implementing"`
   - **revise**: create `revision_requested` event with comment payload, return `next_phase="planning"`
   - **question**: post answer as PR comment, return `next_phase="plan_review"` (no transition)

### Implementation Handler

1. Ensure workspace exists, checkout branch, pull latest
2. Read the approved plan file
3. Build implementation prompt with: plan content, feedback (if revision)
4. Run agent via `agent_service.run_implementation()` - orchestrator dispatches subagents per task
5. Commit changes, push
6. Mark PR as ready for review (convert from draft)
7. Post summary comment
8. Return `PhaseResult(next_phase="code_review")`

### Code Review Handler

1. Interpret the comment via `agent_service.interpret_comment()`
2. Based on intent:
   - **approve**: post final comment, cleanup workspace, return `next_phase="completed"`
   - **revise**: create `revision_requested` event, return `next_phase="implementing"`
   - **back_to_planning**: reset `plan_approved`, revert PR to draft, reset branch to plan commit, create `revision_requested` event, return `next_phase="planning"`
   - **question**: post answer, return `next_phase="code_review"`

### Key decisions
- Handlers are stateless. All state from issue/event parameters and DB.
- Review handlers create `revision_requested` events to drive the next active phase.
- Planning handler handles both initial planning and revisions transparently.
- Implementation handler pulls/rebases before starting revisions to avoid conflicts.
- On back-to-planning: branch is reset to plan commit (via stored hash), PR reverted to draft.
- Commit messages vary: `"docs: plan for issue #N"` vs `"feat: implement..."` vs `"fix: address review feedback..."`.

## Agent Service

Single module encapsulating all Claude Agent SDK interaction.

### Entry Points

| Method | Purpose | Model | Tools |
|--------|---------|-------|-------|
| `run_planning` | Create/revise plan document | opus | Read, Glob, Grep, Write, Edit, Bash, WebSearch, Agent |
| `run_implementation` | Orchestrate implementation | haiku | Read, Glob, Grep, Bash, Agent (no Write/Edit - strict delegation) |
| `interpret_comment` | Classify PR comment intent | sonnet | Custom `classify_comment` MCP tool only |

### Core Execution

All methods go through `_run_query()` which:
- Creates an `agent_runs` record before execution
- Iterates the `query()` async generator, collecting `ResultMessage` data
- Records session_id, cost, tokens on completion (success or error)
- Supports session resumption: on retry, looks up last session for the phase and passes `resume=session_id`

### Comment Classification

Uses a custom MCP tool for structured output instead of parsing free text:

```python
@tool("classify_comment", "Classify a PR comment's intent",
      {"intent": str, "response": str})
async def classify_comment(args):
    return {"content": [{"type": "text", "text": json.dumps(args)}]}

# Registered via create_sdk_mcp_server, allowed as "mcp__review__classify_comment"
```

### Subagent Definitions

**Planning phase:**
- `codebase-explorer` (haiku, read-only) - explores repo structure and conventions

**Implementation phase:**
- `implementer` (sonnet) - implements individual tasks with Write/Edit/Bash/Read/Glob/Grep
- `spec-reviewer` (sonnet, read-only) - verifies implementation matches spec exactly
- `code-reviewer` (sonnet, read-only) - reviews code quality, testing, maintainability

### Key decisions
- `bypassPermissions` on all invocations (sandboxed environment).
- Implementation orchestrator has no Write/Edit tools - forces delegation to subagents, ensuring two-stage review.
- Subagents follow the superpowers pattern: sequential task execution, spec compliance before code quality, max 3 review iterations, escalation = raise exception.
- `interpret_comment` uses `max_turns=1`, `max_budget_usd=0.50`, and a single custom tool.
- Agent `cwd` is set to the workspace path so file operations target the correct repo.
- SDK version pinned to `>=0.1.50` for API compatibility.

## System Prompts

### Planning Prompt

Instructs the agent to:
1. Read the issue carefully
2. Explore the codebase using the `codebase-explorer` subagent
3. Design a solution following existing patterns
4. Write a structured plan document to `docs/plans/issue-{N}-plan.md`

Plan format includes: goal, architecture, per-task breakdown with file paths, TDD steps, testing strategy, and risks. Each task is 2-5 minutes of independently implementable work.

On revision: incorporates feedback while preserving approved parts. The previous plan content and feedback comment are injected into the user prompt.

### Implementation Prompt

Instructs the orchestrator to:
1. Read the plan document
2. For each task: dispatch `implementer` subagent with full task text (not file reference) + scene-setting context
3. After each task: dispatch `spec-reviewer` to verify compliance
4. After spec compliance: dispatch `code-reviewer` for quality
5. Max 3 iterations per review loop; escalate (raise exception) if unresolved
6. After all tasks: run full test suite
7. On revision: focus on specific changes requested in feedback

### Comment Interpretation Prompt

Classifies intent into: `approve`, `revise`, `question`, `back_to_planning` (code_review only).
- Defaults to `revise` when uncertain
- For questions: includes a response in the classification
- Valid intents are parameterized per phase (user prompt specifies which are valid)

## GitHub Service

Thin wrapper over `gh` CLI. All methods go through `_run_gh()` subprocess runner.

### Methods

| Method | gh Command |
|--------|-----------|
| `list_issues` | `gh issue list --label {label} --json number,title,body,author` |
| `get_pr_comments` | `gh api repos/{owner}/{repo}/issues/{pr}/comments` (parsed in Python) |
| `create_pr` | `gh pr create --title --body --head [--draft]` |
| `mark_pr_ready` | `gh pr ready {number}` |
| `mark_pr_draft` | `gh pr ready {number} --undo` |
| `post_comment` | `gh issue comment {number} --body` |
| `clone_repo` | `gh repo clone {owner}/{repo} {path}` |
| `detect_default_branch` | `gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name'` (cached) |

### Key decisions
- No `httpx` or REST API calls. `gh` CLI is the sole interface.
- PR comments use the issues API endpoint (regular comments, not review comments). Human should use the standard PR comment box.
- `author.login` extracted from structured JSON responses for user allowlist checks.
- Default branch detected dynamically and cached per repo.

## Workspace Manager

Manages local checkouts of target repositories at `{base_dir}/{owner}/{repo}/issue-{N}/`.

### Operations

| Method | Purpose |
|--------|---------|
| `ensure_workspace` | Clone if new, fetch+pull if exists. Detects default branch dynamically. |
| `ensure_branch` | Create or checkout branch. Pulls from origin on existing branches. |
| `commit_and_push` | `git add -A`, commit if changes exist, push with `-u`. |
| `reset_to_plan_commit` | `git reset --hard {plan_commit_hash}` + force push. For back-to-planning flow. |
| `cleanup` | Remove workspace directory (on completion). |

### Key decisions
- Workspace path includes issue number for full isolation.
- Sets `git config user.name` and `user.email` after cloning for agent attribution.
- `ensure_branch` pulls from origin on re-entry to catch any manual human pushes.
- `git add -A` is safe since workspace is isolated per-issue.
- Force push in `reset_to_plan_commit` is acceptable on the agent's own branch.
- `shutil.rmtree` for cleanup (synchronous, acceptable at personal project scale).

## Error Handling

### Exception Hierarchy

```
RemoteAgentError (base)
├── GitHubError      # gh CLI failures
├── GitError         # git operation failures
├── AgentError       # Claude Agent SDK failures
└── BudgetExceededError  # Daily budget limit
```

### Error Layers

1. **Agent SDK errors** - caught in `AgentService._run_query()`, recorded in `agent_runs`, re-raised
2. **Phase handler errors** - caught in `Dispatcher._process_event()`, issue transitions to `error`, posted to GitHub
3. **GitHub/Git errors in error handler** - caught separately, logged, do not cascade
4. **Poller errors** - per-repo isolation, one repo's failure doesn't block others
5. **Fatal errors** - log and exit, process supervisor restarts

### Startup Recovery

On boot, `recover_interrupted_issues()` scans for issues in active phases (`planning`, `implementing`) with no unprocessed events. These are transitioned to `error` with message "interrupted by restart" so the human can retry.

## Main Loop

```python
async def main():
    config = Config.load("config.yaml")        # Fail fast
    db = await Database.initialize(config)
    github = GitHubService()
    workspace_mgr = WorkspaceManager(config, github)
    agent_service = AgentService(config, db)
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr)

    await dispatcher.recover_interrupted_issues()  # Startup recovery

    while True:
        try:
            await poller.poll_once()
            await dispatcher.process_events()
        except Exception as e:
            logger.exception("Unexpected error in main loop: %s", e)
        await asyncio.sleep(config.polling.interval_seconds)
```

`KeyboardInterrupt` handled at module level wrapping `asyncio.run(main())`.

## Testing Strategy

- **Unit tests** for each module with mocked dependencies
- **Phase handler tests** mock GitHub and Agent services, verify correct transitions
- **Integration test** - full happy path: new issue -> planning -> plan_review -> approve -> implementing -> code_review -> approve -> completed
- **Error recovery test** - planning fails -> error -> retry comment -> planning succeeds -> continues
- **Budget exceeded test** - verifies event stays unprocessed, notification flag prevents repeated comments
- pytest + pytest-asyncio

## Dependencies

```toml
[project]
name = "remote-agent"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "claude-agent-sdk>=0.1.50",
    "aiosqlite>=0.20.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
```

Three runtime dependencies. No web framework, ORM, or task queue.
