---
source_url: https://github.com/jhonnold/remote-agent/blob/main/docs/superpowers/specs/2026-03-26-logging-and-traceability-design.md
ingested: 2026-06-03
source_type: plan
author: Jay + Claude
synthesis: done
---

# Logging and Traceability Design — remote-agent

**Source:** `docs/superpowers/specs/2026-03-26-logging-and-traceability-design.md` in
[[remote-agent]] (`jhonnold/remote-agent`, branch `main`).
**Date:** 2026-03-26
**Author:** Jay + Claude

The **logging and observability convention** for [[remote-agent]]. Where
[[2026-03-26-github-agent-design]] lays down the poller → events →
dispatcher → phase-handler backbone, this spec defines how that backbone
**talks about itself**: structured JSON logs, correlation IDs threaded
through async context, and a durable audit trail for every consequential
action the agent takes. It is the upstream policy that later designs —
notably [[2026-05-21-remote-agent-issue-5-output-visibility-design]] —
build on rather than re-litigate.

## Problem

The agent had only **~13 log statements across 8 modules**, and the most
consequential modules (`github.py`, `db.py`, `workspace.py`,
`planning.py`, `implementation.py`) had **zero logging**. There was no
structured format, no correlation IDs, no audit trail, and no way to trace
a single issue's journey through the seven-phase state machine. Log level
was hardcoded to `INFO` — changing it meant editing code.

This is acute for an agent that **autonomously writes code and opens
PRs**: without an audit record, there is no after-the-fact answer to "what
did the agent actually do, and did it succeed?"

Three goals shape the design:

1. **Debugging** — see what happened when something breaks in development.
2. **Operational observability** — monitor the agent in production, catch
   problems early.
3. **Auditability** — a clear, durable record of every action taken,
   especially autonomous code writing and PR creation.

## Decisions

The load-bearing choices, each with its rationale:

| Decision | Choice | Why |
|---|---|---|
| Log format | **JSON lines** | Machine-parseable; works with `jq` and aggregators |
| Audit storage | **SQLite table + append-only JSONL file** | DB for querying, file for durable archive |
| Correlation | **Issue ID + Event ID + Operation ID** hierarchy | Filter at any granularity |
| Agent SDK detail | **Metadata only** (model, session, tokens, cost, duration) | Full transcripts are large and may leak repo content |
| Subprocess logging | **Command + exit code; stdout/stderr on failure only** | Lean on the happy path, detailed on errors |
| New dependencies | **None** | A stdlib `JsonFormatter` subclass replaces `python-json-logger` |

The **zero-new-dependency** stance is consistent with the founding
design's minimal-dependency discipline (three runtime deps total). The
**metadata-only SDK logging** decision is the seed that
[[2026-05-21-remote-agent-issue-5-output-visibility-design]] later builds
on — full prompt/result transcripts are deliberately excluded from `INFO`
and become an opt-in DEBUG concern.

## Key approach

### 1. Logging infrastructure — `logging_config.py` (new module)

Three pieces, all stdlib:

- **`JsonFormatter`** — a ~15-line `logging.Formatter` subclass emitting
  JSON lines (`time`, `level`, `logger`, `message`, plus correlation
  fields). Omits `None` fields. No external dependency.
- **`CorrelationFilter`** — a `logging.Filter` that reads three
  `contextvars.ContextVar`s and injects them into every record:
  `current_issue_id` and `current_event_id` (set by the dispatcher per
  event) and `current_operation_id` (reserved for future use). All lookups
  use `.get(None)`, so an unset var never raises `LookupError`.
- **`setup_logging(config)`** — called once from `main.py:run()`,
  replacing the old `logging.basicConfig()`. Level precedence:
  `LOGLEVEL` env var → `config.logging.level` → `"INFO"`. Configures two
  handlers: console JSON to stdout, and a rotating app-log file (10 MB,
  3 backups) at `config.logging.file`, resolved relative to the config
  directory (the same convention `database.path` already uses).

**Correlation via copied context.** The dispatcher achieves per-event
isolation with `contextvars.copy_context()` plus
`asyncio.create_task(coro, context=ctx)`, awaited immediately so sequential
processing is preserved. Because the context is scoped to the task's
lifetime, **no manual token cleanup is needed** — a clean fit with the
"one issue at a time, sequentially" constraint from the founding design.

`config.py` gains a `LoggingConfig` dataclass (`level`, `file`,
`audit_file`) wired into `Config` as an **optional** `logging:` section;
`load_config()` uses `raw.get("logging", {})` so existing config files
without the section keep working.

### 2. Audit system — `audit.py` (new module)

The audit trail is the auditability goal made concrete. **Dual storage**:

- A new **`audit_log` SQLite table** (queryable) with columns for
  `issue_id`, `event_id`, `category`, `action`, `detail`, `duration_ms`,
  `success`, `error_message`. `event_id` is **nullable with no FK** —
  records may be created outside any event (e.g. startup recovery), where
  the ContextVar default is `None`.
- An append-only **`audit.jsonl`** file (durable archive) — never rotated
  or truncated.

Categories: `phase_transition`, `github_api`, `git_op`,
`comment_classification`. **`agent_run` is deliberately excluded** — the
existing `agent_runs` table already captures session/cost/token/timing
metadata, so duplicating it would violate the founding design's
"`agent_runs` is the single source of truth for `session_id`" principle.

**`AuditLogger`** holds a `Database` instance and a persistent file
handle. Its `async log(category, action, *, ..., success, ...)` makes
`success` a **required keyword argument with no default** — callers must
explicitly state success or failure, so the signal can't be silently
dropped. `issue_id`/`event_id` fall back to the ContextVars when not
passed.

**Write order: file first, then DB.** If the file write fails it raises
before touching the DB; if the DB write fails, the file (the authoritative
archive) still holds the record. This is a deliberate one-directional,
recoverable partial-failure mode. File writes are **synchronous, flushed
appends** — explicitly modeled on the founding design's intentionally
synchronous `WorkspaceManager.cleanup()`.

**Integration without breaking existing code.** `Dispatcher.__init__` and
each phase-handler constructor gain `audit: AuditLogger | None = None` as
an **optional** trailing parameter, so existing test fixtures keep working
untouched. Handlers guard with `if self.audit:`. Crucially,
`github.py`/`workspace.py` do **not** call audit directly — they log via
`logging`, and **the calling phase handler decides what is
audit-worthy**, preserving the founding design's layering discipline.
The dispatcher's error path logs an audit record with `success=False`.

### 3. Module-level logging — the INFO/DEBUG contract

The guiding principle: **`INFO` tells the story of what happened; `DEBUG`
exposes the internal mechanics.** Production runs at `INFO`; debugging uses
`LOGLEVEL=DEBUG`. This INFO/DEBUG split is the convention later docs cite.

Highlights of the per-module additions:

- **`github.py`** — `DEBUG` logs the `gh` command, with `--body`/`--title`
  values **masked as `<{n} chars>`** to keep sensitive repo content out of
  logs. No WARNING on failure; the exception propagates to the dispatcher,
  which logs the full stack trace.
- **`workspace.py`** — `INFO` for the human-visible git milestones
  (clone, update, branch create, push); `DEBUG` for raw git commands and
  cleanup.
- **`agent.py`** — `INFO` bracketing each query (start with phase/model,
  completion with cost/tokens/session); `WARNING` on error, kept alongside
  the dispatcher's generic log because it adds phase/model context.
- **`db.py`** — uniform `DEBUG` on **all write operations**, none on reads
  (cheap, frequent SELECTs; callers log what they retrieve).
- **`dispatcher.py`** — adds an `INFO` startup-recovery summary and an
  unconditional `DEBUG "Fetched {n} events"` (including `n=0`) so DEBUG
  users can confirm the poll loop is alive.
- **Phase handlers** — `PlanningHandler`/`ImplementationHandler` get
  entry/exit `INFO`; the passive review handlers (`PlanReviewHandler`,
  `CodeReviewHandler`) **deliberately get none**, since they already log
  their interpretation result and entry/exit would be noise.
- **`main.py`** — **two-phase logging setup** solves a chicken-and-egg
  problem: `config` isn't available until after `create_app()`. So `run()`
  first calls `logging.basicConfig(level=INFO)` with only a `StreamHandler`
  for startup, then `setup_logging(config)` swaps in the JSON formatter,
  rotating file handler, and config-driven level. `AuditLogger.close()`
  joins `db.close()` in the `finally` block.

### 4. Testing strategy

Follows existing project patterns: real SQLite via `tmp_path`, all
external I/O mocked, `AsyncMock` for async deps, no `@pytest.mark.asyncio`
markers (`asyncio_mode = "auto"`).

- **`test_logging_config.py`** — `JsonFormatter` field correctness,
  `CorrelationFilter` inject/omit behavior, `setup_logging` end-to-end
  (record appears in both stream and temp file), `LOGLEVEL` override and
  config fallback. A **shared ContextVar-cleanup fixture** resets all three
  vars in teardown to prevent cross-test contamination.
- **`test_audit.py`** — the file-first guarantee is the headline test:
  patch the file handle's `write` to raise `IOError` and assert the DB
  mock's `execute` was **never called**. Plus JSONL validity, ContextVar
  fallback, `duration_ms` present/NULL, and `close()` flushing.
- **Existing phase-handler / dispatcher tests** — unchanged because
  `audit` defaults to `None`; one new test per handler asserts the
  phase-boundary audit record matches the `PhaseResult`. The dispatcher
  gains a context-isolation test (two events, different issues, captured
  ContextVar values map correctly) and an error-path `success=False` test.
- **Integration test** — wires a real `tmp_path` JSONL file and asserts
  one audit record per transition across the full lifecycle, each with
  `success=True`.
- **Explicitly not tested:** individual `logger.info()`/`logger.debug()`
  calls — they are stdlib calls, brittle to assert on, and low value. The
  non-trivial correctness lives in `JsonFormatter`/`CorrelationFilter`,
  which **are** tested; the log statements are verified by running at
  `LOGLEVEL=DEBUG`. This is the testing-posture policy that
  [[2026-05-21-remote-agent-issue-5-output-visibility-design]] cites.

## Relationship to other designs

- **Founds the observability layer atop** the
  [[2026-03-26-github-agent-design]] architecture — it consumes that
  design's dispatcher, `agent_runs` table, layering discipline, and
  sequential-processing constraint rather than changing any of them.
- **Establishes the conventions** that
  [[2026-05-21-remote-agent-issue-5-output-visibility-design]] depends on:
  the metadata-only-vs-transcript split (issue #5 adds the opt-in DEBUG
  transcripts this doc deliberately deferred), the reuse of `JsonFormatter`
  and `CorrelationFilter`, and the "don't unit-test individual log calls"
  testing posture.
