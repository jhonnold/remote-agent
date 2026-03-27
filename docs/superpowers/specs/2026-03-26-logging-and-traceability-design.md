# Logging and Traceability Design

## Problem

The remote-agent has ~13 log statements across 8 modules. Several critical modules (`github.py`, `db.py`, `workspace.py`, `planning.py`, `implementation.py`) have zero logging. There is no structured logging, no correlation IDs, no audit trail, and no way to trace a single issue's journey through the system. The log level is hardcoded to INFO with no way to enable DEBUG without editing code.

## Goals

1. **Debugging** — understand what's happening when things go wrong during development
2. **Operational observability** — monitor the agent in production, catch problems early
3. **Auditability** — clear record of every action the agent took (especially important for autonomous code writing and PR creation)

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Structured logging format | JSON lines | Machine-parseable, works with `jq` and log aggregation tools |
| Audit storage | SQLite table + append-only JSONL file | DB for querying, file for durable archive |
| Correlation | Issue ID + Event ID + Operation ID hierarchy | Filter at any granularity |
| Agent SDK detail level | Metadata only (model, session, tokens, cost, duration) | Full transcripts are too large and may contain sensitive repo content |
| Subprocess logging | Command + exit code; stdout/stderr on failure only | Lean on happy path, detailed on errors |
| New dependencies | None | Stdlib `JsonFormatter` subclass replaces `python-json-logger` |

---

## Section 1: Logging Infrastructure

### New module: `src/remote_agent/logging_config.py`

**`JsonFormatter`** — ~15-line `logging.Formatter` subclass. Emits JSON lines with fields: `time`, `level`, `logger`, `message`, plus correlation fields when present. Omits fields that are `None`. No external dependency.

**`CorrelationFilter`** — a `logging.Filter` that reads three `contextvars.ContextVar`s and injects them into every log record:
- `current_issue_id` — set by the dispatcher per event
- `current_event_id` — set by the dispatcher per event
- `current_operation_id` — reserved for future use

Uses `.get(None)` for all ContextVar lookups — never raises `LookupError` when unset.

**`setup_logging(config)`** — called once from `main.py:run()`, replacing the existing `logging.basicConfig()` block. Reads log level from:
1. `LOGLEVEL` env var (highest priority)
2. `config.logging.level` (fallback)
3. `"INFO"` (default)

Configures two handlers:
- **Console:** JSON lines to stdout via `JsonFormatter`
- **App log file:** JSON lines to rotating file via `JsonFormatter`. File path from `config.logging.file`, resolved relative to the config file directory (same as `database.path`). 10 MB max, 3 backups (preserving existing behavior).

### Changes to `config.py`

New `LoggingConfig` dataclass:
```python
@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "remote-agent.log"
    audit_file: str = "audit.jsonl"
```

Added to `Config` as `logging: LoggingConfig`. The `logging:` section is optional in `config.yaml` — defaults are used when absent. File paths resolved relative to config directory, consistent with `database.path`.

`load_config()` must also be updated: the `Config(...)` constructor call (currently line 85) must pass `logging=LoggingConfig(**raw.get("logging", {}))`. The `get()` with default `{}` ensures existing config files without a `logging:` section continue to work.

Example `config.yaml` addition (optional):
```yaml
logging:
  level: INFO
  file: remote-agent.log
  audit_file: audit.jsonl
```

### Changes to `dispatcher.py`

Per-event context isolation via `contextvars.copy_context()` + `asyncio.create_task(coro, context=ctx)`, awaited immediately to preserve sequential processing:

```python
for event in events:
    ctx = contextvars.copy_context()
    ctx.run(current_issue_id.set, event.issue_id)
    ctx.run(current_event_id.set, event.id)
    await asyncio.create_task(
        self._process_event(event), context=ctx
    )
```

No manual token cleanup required — context is automatically scoped to the task's lifetime.

---

## Section 2: Audit System

### New DB table: `audit_log`

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    issue_id INTEGER,
    event_id INTEGER,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    duration_ms INTEGER,
    success INTEGER NOT NULL,
    error_message TEXT,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
    -- event_id is nullable and has no FK constraint: audit records may be
    -- created outside the context of a specific event (e.g., startup recovery),
    -- and the ContextVar default is None. The field is informational, populated
    -- from the ContextVar when available.
);
CREATE INDEX IF NOT EXISTS idx_audit_log_issue_id ON audit_log(issue_id);
```

**Categories:** `phase_transition`, `github_api`, `git_op`, `comment_classification`

Note: `agent_run` is excluded as a category — the existing `agent_runs` table already captures AI execution metadata (session ID, cost, tokens, timing). No duplication.

### New file: `audit.jsonl`

Append-only JSON lines file. Path configurable via `config.logging.audit_file` (default `"audit.jsonl"`), resolved relative to config directory. Never rotated or truncated — this is the durable archive.

### New module: `src/remote_agent/audit.py`

**`AuditLogger`** class:
- Initialized with `Database` instance and audit file path
- Opens a persistent file handle; `close()` flushes and closes it
- `close()` called in `main.py`'s `finally` block alongside `db.close()`

**`async log(category, action, *, issue_id=None, event_id=None, detail=None, duration_ms=None, success, error_message=None)`**

`success` has no default — it is a required keyword argument. Callers must explicitly pass `success=True` or `success=False`. This prevents accidentally omitting the success/failure signal on audit records.
- Reads `current_issue_id` and `current_event_id` from ContextVars as defaults when not passed explicitly (using `.get(None)`)
- **Write order: file first, then DB.** If file write fails, raises before DB write occurs. If DB write fails, the file (authoritative archive) still has the record. One-directional partial failure, recoverable.
- File writes are synchronous appends (single line, flushed immediately). Analogous to `WorkspaceManager.cleanup()` being intentionally synchronous.
- DB writes are single `INSERT` statements

### Integration

- `AuditLogger` created in `main.py:create_app()`, passed to `Dispatcher`
- `Dispatcher.__init__` gains `audit: AuditLogger | None = None` as a sixth parameter. Updated signature: `__init__(self, config, db, github, agent_service, workspace_mgr, audit=None)`
- `Dispatcher.__init__` passes `audit` to each phase handler constructor (lines 25-28)
- Each phase handler constructor gains `audit: AuditLogger | None = None` as the last parameter. Handlers check `if self.audit:` before calling. The optional default means existing test fixtures continue to work without changes unless testing audit behavior.
- **Call sites requiring updates:**
  1. `main.py:create_app()` — pass `audit` to `Dispatcher(...)`
  2. `tests/test_dispatcher.py` dispatcher fixture — existing fixture continues to work (param is optional)
  3. `tests/test_integration.py` line 51 — pass `audit` to `Dispatcher(...)` to wire up integration audit testing
- Phase handlers call `audit.log()` for key domain actions (PR creation, branch push, comment classification, phase transitions)
- `Dispatcher._process_event` error path also calls `audit.log()` with `success=False`
- `github.py` and `workspace.py` do NOT call audit directly — they log via `logging`. The calling phase handler decides what's audit-worthy.

---

## Section 3: Module-Level Logging Additions

### Principle

`INFO` gives you the story of what happened. `DEBUG` gives you the internal mechanics. Production runs at `INFO`; debugging uses `LOGLEVEL=DEBUG`.

### `github.py` — `_run_gh` method
- `DEBUG`: command args being run, with `--body` and `--title` values masked as `<{n} chars>` to prevent sensitive content in logs
- No WARNING on failure — the exception propagates to the dispatcher which logs it with full stack trace

### `workspace.py`
- `_run_git`: `DEBUG` on git command being run
- `ensure_workspace`: `INFO` "Cloning {repo} into {path}" (clone path) or `INFO` "Updating workspace for {repo}" (fetch/pull path)
- `ensure_branch`: `INFO` "Created branch {name}"
- `commit_and_push`: `INFO` "Pushed to branch {branch}"
- `cleanup`: `DEBUG` "Cleaned up workspace {path}"

### `agent.py` — `_run_query`
- `INFO` at start: "Starting {phase} query for issue {issue_id}, model={model}"
- Existing resume log (line 127) kept in place — it already fires inside `_run_query` after the session lookup. The new "Starting" INFO is added before the resume check, so the sequence is: "Starting {phase} query..." then conditionally "Resuming session..." then "Completed {phase} query..."
- `INFO` at completion: "Completed {phase} query for issue {issue_id}, cost=${cost}, tokens={input}+{output}, session={session_id}"
- `WARNING` on error: "Query failed for issue {issue_id} phase={phase}: {error}" — intentionally kept alongside dispatcher's exception log because it adds phase/model context the generic catch lacks

### `db.py`
- `DEBUG` on all write operations uniformly: `create_issue`, `update_issue_phase`, `update_issue_branch`, `update_issue_pr`, `update_issue_workspace`, `update_issue_error`, `set_plan_approved`, `set_plan_commit_hash`, `set_budget_notified`, `update_last_comment_id`, `create_event`, `create_comment_events`, `mark_event_processed`, `create_agent_run`, `complete_agent_run`
- No logging on read operations (pure SELECTs are cheap and frequent; callers log what they retrieve)

### `dispatcher.py`
- Existing `INFO` for event processing — kept as-is
- `INFO`: "Recovered {n} interrupted issues on startup" — summary after the existing per-issue WARNINGs
- `DEBUG`: "Fetched {n} events to process" — logged unconditionally (including `n=0`) so that DEBUG users can confirm the poll loop is alive
- Existing `WARNING`/`exception` on event processing failure — kept

### `poller.py`
- No changes — already has reasonable INFO/exception logging

### Phase handlers
- `PlanningHandler` and `ImplementationHandler`: `INFO` at handler entry ("Handling {phase} for issue {issue_id}") and exit ("Completed {phase} for issue {issue_id}")
- `PlanReviewHandler` and `CodeReviewHandler`: no entry/exit logging — they already log the interpretation result, and the dispatcher logs the event. Entry/exit would be redundant noise.
- `DEBUG` for intermediate steps (reading plan file, interpreting comments, etc.)
- Phase handlers make audit calls for key domain actions

### `main.py`
- `run()` uses two-phase logging setup: (1) call `logging.basicConfig(level=logging.INFO)` at the top with only a `StreamHandler` — no file handler — for minimal console logging during startup; then (2) after `create_app()` returns, call `setup_logging(config)` which removes the bootstrap handler and installs the JSON formatter, rotating file handler, and config-driven level. This resolves the chicken-and-egg problem where `config` is not available until after `create_app()`.
- Existing startup and shutdown INFO logs — kept as-is
- `AuditLogger.close()` called in the `finally` block alongside `db.close()`

---

## Section 4: Testing Strategy

### Principle

Follow existing patterns: real SQLite via `tmp_path`, all external I/O mocked, `AsyncMock` for async dependencies, no `@pytest.mark.asyncio` markers.

### New test file: `tests/test_logging_config.py`

- `JsonFormatter` produces valid JSON with expected fields, tested via `logging.LogRecord()` construction
- `CorrelationFilter` injects `issue_id`, `event_id` when ContextVars are set
- `CorrelationFilter` omits correlation fields when ContextVars are unset
- `setup_logging(config)` behavioral test: emit a test log record, verify JSON appears in both captured stream and temp file
- `LOGLEVEL` env var overrides config level
- Config-level fallback works when env var absent

**ContextVar cleanup fixture** — shared fixture used by all tests in this file that resets all three ContextVars in teardown, preventing cross-test contamination.

### New test file: `tests/test_audit.py`

- `AuditLogger.log()` writes to JSONL file first, then to DB — both contain matching records
- JSONL records are valid JSON, one per line, with all expected fields
- DB records use `created_at` default
- ContextVar fallback: `issue_id`/`event_id` populated from ContextVars when not passed explicitly
- ContextVar fallback: fields are `None` when ContextVars unset and no explicit args
- `duration_ms` field: one test passes it and verifies storage, one omits and verifies `NULL`
- `AuditLogger.close()` flushes and closes file handle
- File-first guarantee: initialize `AuditLogger` with real file, patch `audit_logger._file.write` to raise `IOError`, assert DB mock's `execute` was not called
- Uses real SQLite via `tmp_path` for DB, `tmp_path / "audit.jsonl"` for file
- Shares ContextVar cleanup fixture with `test_logging_config.py`

### Updates to existing phase handler tests

- Existing tests unchanged — `audit` parameter defaults to `None`
- One new test per handler verifying the final phase-boundary audit record matches the `PhaseResult` outcome
- `test_planning.py` additionally tests the PR creation audit call
- Uses `AsyncMock` for `AuditLogger` (`log()` is `async def`)

### Updates to `tests/test_dispatcher.py`

- Context isolation test: mock handler captures ContextVar values mid-execution via a callback that reads `current_issue_id.get(None)`. Process two events for different issues, assert captured values map correctly.
- Dispatcher error path calls `audit.log()` with `success=False`
- Existing fixture unchanged — `audit` parameter is optional

### Integration test update

- `AuditLogger` wired in with real `tmp_path` JSONL file
- After lifecycle completes, assert audit file contains one record per transition: `new->planning`, `planning->plan_review`, `plan_review->implementing`, `implementing->code_review`, `code_review->completed`, each with `success=True`

### Not tested

Individual `logger.info()`/`logger.debug()` calls in modules are not unit-tested. They are standard library calls — testing them requires capturing log output and asserting on message content, which is brittle and low value. The `JsonFormatter` and `CorrelationFilter` infrastructure tests cover the non-trivial correctness questions. The log statements themselves are verified by running the app at `LOGLEVEL=DEBUG`.
