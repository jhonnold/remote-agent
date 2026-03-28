# Improve Commit Messages Design

**Issue:** #7
**Goal:** Make the agent's commit messages describe the actual code changes and include GitHub issue-closing keywords (`Closes #N` / `Refs #N`).

## Architecture

The change touches two layers of the system, with an optional cosmetic improvement in a third:

1. **Prompt layer** (`src/remote_agent/prompts/designing.py`, `src/remote_agent/prompts/implementation.py`): Instruct the designing and implementation LLMs to emit a `<commit_message>` XML tag containing a conventional-commit-formatted summary of their changes as part of their final response.

2. **Phase handler layer** (`src/remote_agent/phases/designing.py`, `src/remote_agent/phases/implementation.py`): Capture the `AgentResult` return value (currently discarded by both handlers), parse the `<commit_message>` from `result.result_text`, assemble the final message with a `Closes #N` or `Refs #N` trailer, and pass it to `commit_and_push()`. A new utility module centralizes the parsing and fallback logic.

3. **Planning prompt** (`src/remote_agent/prompts/planning.py`, cosmetic): Update the example commit message in the plan template to model descriptive messages. This propagates through the `implementer` subagent at runtime, but since the phase handler overwrites all workspace changes with its own `commit_and_push()` call, this does **not** affect the final commit on the branch. It is included only as a best-practice alignment.

**Key design decisions:**
- **No extra LLM calls.** The agent LLM already knows what it changed — we extract its summary rather than running a separate diff-summarization call.
- **`WorkspaceManager` and `AgentService` remain unchanged.** All new logic lives in the phase handlers and a new utility module.
- **Graceful degradation.** If the LLM doesn't produce a `<commit_message>` tag, improved fallback templates (which include the issue title and closing keywords) are used. The fallbacks are strictly better than the current hardcoded messages.

**Scope exclusion:** The planning phase handler (`phases/planning.py`) does not call `commit_and_push()` — it saves the plan to a temp file. It is therefore excluded from this change.

## Components

### 1. `src/remote_agent/commit_message.py` (new module)

**Purpose:** Centralize commit message extraction, validation, formatting, and fallback logic.

**Public API:**

```python
def extract_commit_message(result_text: str | None) -> str | None:
    """Parse the last <commit_message>...</commit_message> from LLM output.

    Uses the LAST occurrence to avoid picking up tags that appear in
    sub-agent output or inside design document content.

    Returns None if:
    - result_text is None
    - No tag found
    - Tag content is empty after stripping whitespace

    Truncates content to 500 characters if it exceeds that limit.
    """

def build_commit_message(
    extracted: str | None,
    issue_number: int,
    issue_title: str,
    *,
    closes: bool,
    is_revision: bool = False,
) -> str:
    """Assemble the final commit message.

    If `extracted` is provided, uses it as the subject line.
    Otherwise, generates a fallback based on phase context.

    Appends a trailer:
    - `Closes #N` when closes=True (implementation commits)
    - `Refs #N` when closes=False (design commits)
    """
```

**Fallback templates** (used when `extracted` is `None`):

| Context | Template |
|---|---|
| Design (new) | `docs: add design for {issue_title} (#{issue_number})` |
| Design (revision) | `docs: revise design for {issue_title} (#{issue_number})` |
| Implementation (new) | `feat: implement {issue_title} (#{issue_number})` |
| Implementation (revision) | `fix: address review feedback for {issue_title} (#{issue_number})` |

The `closes` parameter determines the trailer — the caller (phase handler) makes this semantic decision, keeping the utility module decoupled from phase names.

**Prefix validation:** Out of scope for this issue. The LLM may emit non-standard prefixes like `update:` instead of `feat:`. This is accepted as-is. The project has no changelog generators or CI gates that parse conventional commit prefixes, so the risk is cosmetic only. If prefix enforcement becomes necessary, it can be added to `extract_commit_message()` later without changing the public API.

### 2. `src/remote_agent/prompts/designing.py` (modified)

Add an instruction block to the designing system prompt:

> After completing the design document, emit a commit message summarizing your changes using conventional commit format inside a `<commit_message>` XML tag. The message should describe what the design covers, not just reference the issue number. Example: `<commit_message>docs: add design covering retry logic, timeout configuration, and error propagation</commit_message>`

### 3. `src/remote_agent/prompts/implementation.py` (modified)

Add an instruction block to the implementation system prompt:

> After all tasks are complete and verification passes, emit a commit message summarizing the overall changes using conventional commit format inside a `<commit_message>` XML tag. The message should describe what was implemented, not just reference the issue. Example: `<commit_message>feat: add retry logic with configurable timeouts for API calls</commit_message>`

### 4. `src/remote_agent/prompts/planning.py` (modified, cosmetic)

Update line 57 from:
```
5. Commit: `git add tests/exact/path/test_file.py src/exact/path/file.py && git commit -m 'feat: add X'`
```
to:
```
5. Commit: `git add tests/exact/path/test_file.py src/exact/path/file.py && git commit -m 'feat: add descriptive summary of changes'`
```

This models descriptive messages for the implementer subagent. As noted in Architecture, this does not affect the final branch commit.

### 5. `src/remote_agent/phases/designing.py` (modified)

**Required changes:**
- Import `extract_commit_message` and `build_commit_message` from `remote_agent.commit_message`.
- **Capture the return value** of `self.agent_service.run_designing(...)` (currently discarded at line 52).
- Replace the hardcoded commit message construction (lines 63–65) with:

```python
result = await self.agent_service.run_designing(...)

extracted = extract_commit_message(result.result_text)
commit_msg = build_commit_message(
    extracted, issue.issue_number, issue.title,
    closes=False, is_revision=bool(existing_design),
)
await self.workspace_mgr.commit_and_push(workspace, branch, commit_msg)
```

### 6. `src/remote_agent/phases/implementation.py` (modified)

**Required changes:**
- Same imports as designing.py.
- **Capture the return value** of `self.agent_service.run_implementation(...)` (currently discarded at line 46).
- Replace the hardcoded commit message construction (lines 56–58) with:

```python
result = await self.agent_service.run_implementation(...)

extracted = extract_commit_message(result.result_text)
commit_msg = build_commit_message(
    extracted, issue.issue_number, issue.title,
    closes=True, is_revision=bool(feedback),
)
await self.workspace_mgr.commit_and_push(workspace, issue.branch_name, commit_msg)
```

- Also update the PR body (line 66) from `f"Implementation for #{issue.issue_number}"` to `f"Implementation for #{issue.issue_number}\n\nCloses #{issue.issue_number}"` so the PR description also carries the closing keyword.

## Data Flow

### Design Phase

```
DesigningHandler.handle()
  │
  ├─ agent_service.run_designing(...)
  │    └─ LLM writes design doc, emits:
  │       <commit_message>docs: add design covering X, Y, Z</commit_message>
  │    └─ Returns AgentResult(result_text="....<commit_message>...</commit_message>")
  │
  ├─ result = AgentResult  ← MUST capture return value (currently discarded)
  │
  ├─ extract_commit_message(result.result_text)
  │    └─ Regex finds LAST <commit_message> tag → "docs: add design covering X, Y, Z"
  │    └─ (or returns None if missing/empty)
  │
  ├─ build_commit_message(extracted, 42, "Add auth", closes=False, is_revision=False)
  │    └─ "docs: add design covering X, Y, Z\n\nRefs #42"
  │    └─ (or fallback: "docs: add design for Add auth (#42)\n\nRefs #42")
  │
  └─ workspace_mgr.commit_and_push(workspace, branch, message)
```

### Implementation Phase

```
ImplementationHandler.handle()
  │
  ├─ agent_service.run_implementation(...)
  │    └─ LLM orchestrates subagents, emits:
  │       <commit_message>feat: add retry logic with configurable timeouts</commit_message>
  │    └─ Returns AgentResult(result_text="....<commit_message>...</commit_message>")
  │
  ├─ result = AgentResult  ← MUST capture return value (currently discarded)
  │
  ├─ extract_commit_message(result.result_text)
  │    └─ Regex finds LAST <commit_message> tag
  │
  ├─ build_commit_message(extracted, 42, "Add auth", closes=True, is_revision=False)
  │    └─ "feat: add retry logic with configurable timeouts\n\nCloses #42"
  │
  └─ workspace_mgr.commit_and_push(workspace, branch, message)
```

## Error Handling

| Scenario | Behavior |
|---|---|
| **LLM doesn't emit `<commit_message>` tag** | `extract_commit_message()` returns `None`. `build_commit_message()` uses the fallback template. Graceful degradation — no error raised. |
| **LLM emits empty tag** (`<commit_message>  </commit_message>`) | Content stripped → empty string → returns `None` → fallback. |
| **LLM emits very long message** (>500 chars) | Content is truncated to 500 characters. The truncated message is still used (not rejected) — a long description is better than a generic template. |
| **`result_text` is `None`** (agent error/timeout) | `extract_commit_message(None)` returns `None` immediately → fallback. No crash. |
| **Tag appears in sub-agent output or design doc** | Mitigated by extracting the **last** occurrence. The LLM's final summary response comes after all sub-agent interactions, so the last tag is most likely the intended one. If even this heuristic fails, the fallback template is used. |
| **LLM uses non-standard prefix** (e.g., `update:` instead of `feat:`) | Accepted as-is. Prefix validation is out of scope — the project has no tooling that parses conventional commit prefixes. This is a known limitation documented in the Components section. |
| **Commit message contains special characters** (quotes, newlines in subject) | `commit_and_push()` already passes the message via `git commit -m`, which handles quoting. Newlines in the extracted subject are replaced with spaces to keep the subject line clean. |

## Testing Strategy

### Unit Tests: `tests/test_commit_message.py` (new)

| # | Test | Input | Expected |
|---|---|---|---|
| 1 | `test_extract_valid_tag` | `"...<commit_message>feat: add X</commit_message>"` | `"feat: add X"` |
| 2 | `test_extract_missing_tag` | `"no tag here"` | `None` |
| 3 | `test_extract_empty_tag` | `"<commit_message>  </commit_message>"` | `None` |
| 4 | `test_extract_multiple_tags_returns_last` | Two tags in text | Returns content of the last tag |
| 5 | `test_extract_none_input` | `None` | `None` |
| 6 | `test_extract_truncates_long_content` | Tag with 600-char content | Truncated to 500 chars |
| 7 | `test_build_with_extracted_closes` | `extracted="feat: add X"`, `closes=True` | `"feat: add X\n\nCloses #42"` |
| 8 | `test_build_with_extracted_refs` | `extracted="docs: add Y"`, `closes=False` | `"docs: add Y\n\nRefs #42"` |
| 9 | `test_build_fallback_design_new` | `None`, `closes=False`, `is_revision=False` | `"docs: add design for Add auth (#42)\n\nRefs #42"` |
| 10 | `test_build_fallback_design_revision` | `None`, `closes=False`, `is_revision=True` | `"docs: revise design for Add auth (#42)\n\nRefs #42"` |
| 11 | `test_build_fallback_impl_new` | `None`, `closes=True`, `is_revision=False` | `"feat: implement Add auth (#42)\n\nCloses #42"` |
| 12 | `test_build_fallback_impl_revision` | `None`, `closes=True`, `is_revision=True` | `"fix: address review feedback for Add auth (#42)\n\nCloses #42"` |

### Integration Tests: Update existing + add new

**Existing tests that must be updated** (they assert old hardcoded commit message strings):
- `tests/test_phases/test_designing.py::test_designing_creates_branch_and_posts_design` (line 71–73, asserts `"docs: design for issue #42"`)
- `tests/test_phases/test_designing.py::test_designing_revision_passes_feedback` (asserts `"docs: revise design for issue #42"`)
- `tests/test_phases/test_implementation.py::test_implementation_creates_pr_when_none_exists` (asserts PR body `"Implementation for #42"`)

These must be updated to expect the new `build_commit_message()` output format and the updated PR body.

**New integration tests:**

| # | Test | Setup | Assertion |
|---|---|---|---|
| 13 | `test_designing_uses_llm_commit_message` | Mock `run_designing` returning `AgentResult` with `result_text` containing `<commit_message>docs: add design for auth flow</commit_message>` | `commit_and_push` called with `"docs: add design for auth flow\n\nRefs #42"` |
| 14 | `test_implementation_uses_llm_commit_message` | Mock `run_implementation` returning result with `<commit_message>feat: add OAuth2 endpoints</commit_message>` | `commit_and_push` called with `"feat: add OAuth2 endpoints\n\nCloses #42"` |
| 15 | `test_designing_falls_back_on_missing_tag` | Mock agent returning `result_text` with no tag | `commit_and_push` called with fallback `"docs: add design for Add auth (#42)\n\nRefs #42"` |
| 16 | `test_implementation_falls_back_on_none_result` | Mock agent returning `result_text=None` | `commit_and_push` called with fallback `"feat: implement Add auth (#42)\n\nCloses #42"` |
| 17 | `test_implementation_pr_body_includes_closes` | Mock `run_implementation` for new PR path | `create_pr` called with body containing `Closes #42` |
