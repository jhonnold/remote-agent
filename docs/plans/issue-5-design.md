# Claude Code Output Visibility Design

**Issue:** #5
**Goal:** Add DEBUG-level logging of agent prompts and results in `_run_query()` so operators can inspect what is sent to and received from the Claude Agent SDK.

## Architecture

This is a minimal change scoped entirely to the agent invocation layer. The existing `_run_query()` method in `AgentService` (`src/remote_agent/agent.py`, lines 158–208) is the single point where all Claude Agent SDK calls are made. We add `logger.debug()` calls at two points in this method:

1. **Before the `query()` call** — log the user prompt (`prompt` parameter) and the system prompt (`options.system_prompt`, a readable attribute set by all callers via the `ClaudeAgentOptions` constructor)
2. **Inside the `ResultMessage` branch** — log the `result_text` returned by the agent

This follows the codebase's established logging convention (from the logging-and-traceability design doc): INFO for narrative ("Starting query", "Completed query") and DEBUG for internal mechanics. Operators opt in via the existing `LOGLEVEL=DEBUG` env var or `config.logging.level: DEBUG` in `config.yaml`.

No new modules, configuration fields, or infrastructure are introduced. The existing `JsonFormatter` in `logging_config.py` serializes debug messages into structured JSON, and the existing `CorrelationFilter` attaches `issue_id` and `event_id` context to log records (when running within the dispatcher context, which is the normal execution path).

### Design decisions

- **DEBUG, not INFO:** Prompts and results are internal mechanics, not story-level narrative. The prior logging design (2026-03-26) explicitly excluded full transcripts at INFO. DEBUG provides opt-in visibility without changing the default log experience.
- **Full content, no truncation:** The issue author's complaint is insufficient visibility. Truncation would work against that goal. Operators who enable DEBUG accept verbose output. If log volume becomes a concern under sustained load, truncation can be addressed in a follow-up issue.
- **Flat string format:** The `JsonFormatter` serializes log messages as a flat `"message"` string in JSON. Multi-line prompts and results will appear as escaped strings within that field. Adding structured JSON fields (e.g., separate `"prompt"` and `"result"` keys) is out of scope — it would require `JsonFormatter` changes and is not requested by the issue.
- **Audit system out of scope:** The issue specifically references "logs." The audit trail (`audit.jsonl` + SQLite `audit_log` table) is not mentioned and remains unchanged.

## Components

### 1. `AgentService._run_query()` — `src/remote_agent/agent.py`

**Current responsibility:** Executes a Claude Agent SDK query, extracts result metadata from `ResultMessage`, returns `AgentResult`.

**Change:** Add three `logger.debug()` calls:

1. Before the `query()` loop (after session resumption logic, before line 180): log the user prompt string (`prompt` parameter)
2. Before the `query()` loop: log the system prompt string (`options.system_prompt`)
3. Inside the `if isinstance(message, ResultMessage)` branch (after line 183): log `result_text` (`message.result`)

**Public interface:** Unchanged. `_run_query()` is an internal method; its signature and return type (`AgentResult`) are not modified.

**Dependencies:** No new dependencies. Uses the existing `logger = logging.getLogger(__name__)` at module level.

### 2. `logging_config.py` — No changes

The existing `JsonFormatter` and `CorrelationFilter` handle DEBUG-level messages identically to INFO from a formatting and filtering perspective. No modifications needed.

Note: Multi-line content (prompts, results) will appear as escaped strings within the `"message"` JSON field. This is a known limitation of the flat-string format. Structured fields are explicitly deferred as out of scope.

### 3. `config.py` — No changes

No new configuration fields. The existing `config.logging.level` and `LOGLEVEL` env var provide sufficient verbosity control.

## Data Flow

1. **Input logging** (inserted between line ~171 and line 180 of `_run_query()`):
   - After session resumption logic completes and before `async for message in query(...)`:
   ```python
   logger.debug("Agent prompt for issue %d phase=%s:\n%s", issue_id, phase, prompt)
   logger.debug("Agent system prompt for issue %d phase=%s:\n%s", issue_id, phase, options.system_prompt)
   ```
   - `issue_id` and `phase` are included directly in the format string for immediate readability. The `CorrelationFilter` additionally attaches `issue_id` and `event_id` as structured JSON fields when running within the dispatcher's context (the normal execution path for all phase handlers).

2. **Output logging** (inserted inside the `ResultMessage` branch, after line 183):
   ```python
   logger.debug("Agent result for issue %d phase=%s:\n%s", issue_id, phase, message.result)
   ```
   - Placed inside the `isinstance(message, ResultMessage)` branch. Note that `message.result` may be `None` in edge cases (the SDK does not guarantee a non-None result). This is acceptable — logging `None` is correct behavior as it reflects what the SDK returned.

3. **No output on failure:** If `query()` raises an exception before yielding a `ResultMessage`, no result debug log is emitted. The existing WARNING log at line 202 already covers failure cases with the error message.

## Error Handling

No new error handling is required. The `logger.debug()` calls cannot affect control flow:

- `prompt` is always `str` (guaranteed by all callers: `run_designing`, `run_planning`, `run_implementation`, `answer_question`)
- `options.system_prompt` is always `str` (set via `ClaudeAgentOptions` constructor by all callers)
- `message.result` is `str | None` inside the `ResultMessage` branch — both are safely formatted by `%s`
- Python's `logging` module silently handles formatting errors without propagating exceptions

The debug calls fall inside the existing `try` block (line 179), so any hypothetical failure would be caught by the existing exception handler — which is correct and sufficient behavior.

## Testing Strategy

The project's logging-and-traceability design doc (2026-03-26, section on testing) explicitly states:

> Individual `logger.info()`/`logger.debug()` calls in modules are not unit-tested. They are standard library calls — testing them requires capturing log output and asserting on message content, which is brittle and low value.

This change follows that policy. No new unit tests are added for the debug log statements.

### Verification approach

1. **Manual verification:** Run the agent locally with `LOGLEVEL=DEBUG` and confirm that prompt and result content appears in the log output for each phase (designing, planning, implementing, question answering).

2. **Optional integration smoke test:** If automated coverage is desired in the future, a single test using pytest's built-in `caplog` fixture that calls `_run_query()` directly (with mocked SDK) would be the least brittle approach — consistent with how `CorrelationFilter` is tested in `tests/test_logging_config.py`. This is explicitly deferred and not part of the initial implementation.

### Manual verification checklist

- [ ] `LOGLEVEL=DEBUG` shows user prompt before query starts
- [ ] `LOGLEVEL=DEBUG` shows system prompt before query starts
- [ ] `LOGLEVEL=DEBUG` shows result text after `ResultMessage` is received
- [ ] `LOGLEVEL=INFO` (default) does not show any of the above
- [ ] Log output includes `issue_id` and `phase` for correlation
- [ ] Failed queries do not emit a result debug log
