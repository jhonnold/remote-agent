# Improve Commit Messages Design

**Issue:** #7
**Goal:** Make the agent's commit messages describe the actual code changes and include GitHub issue-closing keywords (`Closes #N` / `Refs #N`).

## Architecture

The change touches two layers of the system, with an optional cosmetic improvement in a third:

1. **Prompt layer** (`src/remote_agent/prompts/designing.py`, `src/remote_agent/prompts/implementation.py`): Instruct the designing and implementation LLMs to emit a `<commit_message>` XML tag containing a conventional-commit-formatted summary of their changes as part of their final response.

2. **Phase handler layer** (`src/remote_agent/phases/designing.py`, `src/remote_agent/phases/implementation.py`): Capture the `AgentResult` return value (currently discarded by both handlers — neither assigns the result of the `await` call), parse the `<commit_message>` from `result.result_text`, assemble the final message with a `Closes #N` or `Refs #N` trailer, and pass it to `commit_and_push()`. A new utility module centralizes the parsing and fallback logic.

3. **Planning prompt** (`src/remote_agent/prompts/planning.py`, cosmetic): Update the example commit message in the plan template to model descriptive messages. This propagates through the `implementer` subagent at runtime, but since the phase handler overwrites all workspace changes with its own `commit_and_push()` call after the LLM finishes, this does **not** affect the final commit on the branch. It is included only as a best-practice alignment.

**Key design decisions:**
- **No extra LLM calls.** The agent LLM already knows what it changed — we extract its summary rather than running a separate diff-summarization call.
- **`WorkspaceManager` and `AgentService` remain unchanged.** All new logic lives in the phase handlers and a new utility module.
- **Graceful degradation.** If the LLM doesn't produce a `<commit_message>` tag, improved fallback templates (which include the issue title and closing keywords) are used. The fallbacks are strictly better than the current hardcoded messages.

**Scope exclusions:**
- The planning phase handler (`phases/planning.py`) does not call `commit_and_push()` — it saves the plan to a temp file via `shutil.move` and transitions directly to `implementing`. It is therefore excluded from this change.
- Conventional commit prefix validation is out of scope. The project has no changelog generators or CI gates that parse prefixes.

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
    Replaces newlines in the extracted subject with spaces.
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

    Appends a trailer (separated by blank line):
    - `Closes #N` when closes=True (implementation commits)
    - `Refs #N` when closes=False (design commits)
    """
```

**Fallback templates** (used when `extracted` is `None`):

| Context | Template |
|---|---|
| Design (new), `closes=False, is_revision=False` | `docs: add design for {issue_title} (#{issue_number})` |
| Design (revision), `closes=False, is_revision=True` | `docs: revise design for {issue_title} (#{issue_number})` |
| Implementation (new), `closes=True, is_revision=False` | `feat: implement {issue_title} (#{issue_number})` |
| Implementation (revision), `closes=True, is_revision=True` | `fix: address review feedback for {issue_title} (#{issue_number})` |

The `closes` parameter determines the trailer — the caller (phase handler) makes this semantic decision, keeping the utility module decoupled from phase names.

**Dependencies:** None. Pure functions using only `re` from the standard library.

### 2. `src/remote_agent/prompts/designing.py` (modified)

Add an instruction block to the end of the system prompt returned by `build_designing_system_prompt()` (currently lines 5–85):

> After completing the design document, emit a commit message summarizing your changes using conventional commit format inside a `<commit_message>` XML tag. The message should describe what the design covers, not just reference the issue number. Example: `<commit_message>docs: add design covering retry logic, timeout configuration, and error propagation</commit_message>`

Note: The current prompt contains no mention of commit messages — this is entirely new content.

### 3. `src/remote_agent/prompts/implementation.py` (modified)

Add an instruction block to the end of the system prompt returned by `build_implementation_system_prompt()` (currently lines 4–71):

> After all tasks are complete and verification passes, emit a commit message summarizing the overall changes using conventional commit format inside a `<commit_message>` XML tag. The message should describe what was implemented, not just reference the issue. Example: `<commit_message>feat: add retry logic with configurable timeouts for API calls</commit_message>`

Note: The current prompt contains no mention of commit messages — this is entirely new content.

### 4. `src/remote_agent/prompts/planning.py` (modified, cosmetic)

Update the example commit in `build_planning_system_prompt()` at line 57 from:
```
5. Commit: `git add tests/exact/path/test_file.py src/exact/path/file.py && git commit -m 'feat: add X'`
```
to:
```
5. Commit: `git add tests/exact/path/test_file.py src/exact/path/file.py && git commit -m 'feat: add descriptive summary of changes'`
```

This models descriptive messages for the `implementer` subagent. As noted in Architecture, intermediate commits made by the implementer during plan execution are superseded by the phase handler's own `commit_and_push()` call, so this change does not affect the final branch commit. It is included for consistency only.

### 5. `src/remote_agent/phases/designing.py` (modified)

**Required changes:**
- Import `extract_commit_message` and `build_commit_message` from `remote_agent.commit_message`.
- **Capture the return value** of `self.agent_service.run_designing(...)`. Currently at line 52, the `await` expression is a bare statement with no assignment. It must become `result = await self.agent_service.run_designing(...)`.
- Replace the hardcoded commit message construction (lines 63–65) with:

```python
# Line 52: capture result (currently discarded)
result = await self.agent_service.run_designing(
    issue_number=issue.issue_number,
    issue_title=issue.title,
    issue_body=issue.body or "",
    cwd=workspace,
    issue_id=issue.id,
    existing_design=existing_design,
    feedback=feedback,
)

# Lines 63-65: replace hardcoded commit message
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
- **Capture the return value** of `self.agent_service.run_implementation(...)`. Currently at line 46, the `await` expression is a bare statement with no assignment. It must become `result = await self.agent_service.run_implementation(...)`.
- Replace the hardcoded commit message construction (lines 56–58) with:

```python
# Line 46: capture result (currently discarded)
result = await self.agent_service.run_implementation(
    plan_content=plan_content,
    design_content=design_content,
    issue_title=issue.title,
    issue_body=issue.body or "",
    cwd=workspace,
    issue_id=issue.id,
    feedback=feedback,
)

# Lines 56-58: replace hardcoded commit message
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
  ├─ result = await agent_service.run_designing(...)
  │    └─ LLM writes design doc, emits:
  │       <commit_message>docs: add design covering X, Y, Z</commit_message>
  │    └─ Returns AgentResult(result_text="...<commit_message>...</commit_message>")
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
  ├─ result = await agent_service.run_implementation(...)
  │    └─ LLM orchestrates subagents, emits:
  │       <commit_message>feat: add retry logic with configurable timeouts</commit_message>
  │    └─ Returns AgentResult(result_text="...<commit_message>...</commit_message>")
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
| **LLM emits very long message** (>500 chars) | Content is truncated to 500 characters. The truncated message is still used (not rejected) — a long description is better than a generic fallback template. |
| **`result_text` is `None`** (agent error/timeout) | `extract_commit_message(None)` returns `None` immediately → fallback. No crash. |
| **Tag appears in sub-agent output or design doc** | Mitigated by extracting the **last** occurrence. The LLM's final summary response comes after all sub-agent interactions, so the last tag is most likely the intended one. If even this heuristic fails, the fallback template is used. |
| **LLM uses non-standard prefix** (e.g., `update:` instead of `feat:`) | Accepted as-is. Prefix validation is out of scope — the project has no tooling that parses conventional commit prefixes. |
| **Commit message contains special characters** (quotes, newlines) | `commit_and_push()` passes the message as a list element to `subprocess` (`["commit", "-m", message]`), so shell quoting is not an issue. Newlines in the extracted subject are replaced with spaces by `extract_commit_message()`. |

## Testing Strategy

### Unit Tests: `tests/test_commit_message.py` (new)

| # | Test | Input | Expected |
|---|---|---|---|
| 1 | `test_extract_valid_tag` | `"...<commit_message>feat: add X</commit_message>"` | `"feat: add X"` |
| 2 | `test_extract_missing_tag` | `"no tag here"` | `None` |
| 3 | `test_extract_empty_tag` | `"<commit_message>  </commit_message>"` | `None` |
| 4 | `test_extract_multiple_tags_returns_last` | Two tags in text | Returns content of the last tag |
| 5 | `test_extract_none_input` | `None` | `None` |
| 6 | `test_extract_truncates_long_content` | Tag with 600-char content | Returns first 500 chars (truncated, not rejected) |
| 7 | `test_extract_replaces_newlines` | Tag with `"feat: add\nsome thing"` | `"feat: add some thing"` |
| 8 | `test_build_with_extracted_closes` | `extracted="feat: add X"`, `closes=True` | `"feat: add X\n\nCloses #42"` |
| 9 | `test_build_with_extracted_refs` | `extracted="docs: add Y"`, `closes=False` | `"docs: add Y\n\nRefs #42"` |
| 10 | `test_build_fallback_design_new` | `None`, `closes=False`, `is_revision=False` | `"docs: add design for Add auth (#42)\n\nRefs #42"` |
| 11 | `test_build_fallback_design_revision` | `None`, `closes=False`, `is_revision=True` | `"docs: revise design for Add auth (#42)\n\nRefs #42"` |
| 12 | `test_build_fallback_impl_new` | `None`, `closes=True`, `is_revision=False` | `"feat: implement Add auth (#42)\n\nCloses #42"` |
| 13 | `test_build_fallback_impl_revision` | `None`, `closes=True`, `is_revision=True` | `"fix: address review feedback for Add auth (#42)\n\nCloses #42"` |

### Existing Tests That Must Be Updated

**Commit message assertions in `tests/test_phases/test_designing.py`** (2 tests):

1. `test_designing_creates_branch_and_posts_design` (lines 71–73): Currently asserts `commit_and_push` called with `"docs: design for issue #42"`. Must be updated to expect the new fallback format: `"docs: add design for Add auth (#42)\n\nRefs #42"`. Note: the existing mock at line 55–57 constructs `AgentResult` without `result_text`, so `result_text` defaults to `None`, which triggers the fallback path.

2. `test_designing_revision_passes_feedback` (lines 112–114): Currently asserts `commit_and_push` called with `"docs: revise design for issue #42"`. Must be updated to expect: `"docs: revise design for Add auth (#42)\n\nRefs #42"`. Same `result_text=None` fallback applies.

**PR body assertion in `tests/test_phases/test_implementation.py`** (1 test):

3. `test_implementation_creates_pr_when_none_exists` (line 79): Currently asserts `body="Implementation for #42"`. Must be updated to expect `body="Implementation for #42\n\nCloses #42"`.

**No commit message assertions exist in `test_implementation.py`** — none of its six tests assert on `commit_and_push`, so no commit message assertion updates are needed there.

**Note:** The `test_designing_audit_records` test also mocks `run_designing` without `result_text`, but it does not assert on `commit_and_push`, so it requires no changes.

### New Integration Tests

| # | Test | File | Setup | Assertion |
|---|---|---|---|---|
| 14 | `test_designing_uses_llm_commit_message` | `test_designing.py` | Mock `run_designing` returning `AgentResult` with `result_text` containing `<commit_message>docs: add design for auth flow</commit_message>` | `commit_and_push` called with `"docs: add design for auth flow\n\nRefs #42"` |
| 15 | `test_implementation_uses_llm_commit_message` | `test_implementation.py` | Mock `run_implementation` returning result with `result_text` containing `<commit_message>feat: add OAuth2 endpoints</commit_message>` | `commit_and_push` called with `"feat: add OAuth2 endpoints\n\nCloses #42"` |
| 16 | `test_designing_falls_back_on_missing_tag` | `test_designing.py` | Mock agent returning `result_text="No tag here"` | `commit_and_push` called with fallback `"docs: add design for Add auth (#42)\n\nRefs #42"` |
| 17 | `test_implementation_falls_back_on_none_result` | `test_implementation.py` | Mock agent returning `result_text=None` | `commit_and_push` called with fallback `"feat: implement Add auth (#42)\n\nCloses #42"` |
| 18 | `test_implementation_pr_body_includes_closes` | `test_implementation.py` | Mock `run_implementation` for new PR path | `create_pr` called with body containing `Closes #42` |
