# Improve Agent Prompts Quality Design

**Issue:** #9
**Goal:** Restructure all agent system prompts to use consistent Role/Task/Format sections and RFC 2119 requirement-level keywords.

## Architecture

This change restructures all system prompts and sub-agent prompts in `src/remote_agent/prompts/` to follow a consistent format. No runtime behavior, API surface, or control flow changes — only prompt string content is modified.

### Approach: Hybrid Role/Task/Format with RFC 2119

Every system prompt and sub-agent prompt adopts three mandatory top-level sections:

1. **`## Role`** — Identity and purpose. Replaces the current opening paragraph and sections like `## Your Purpose`, `## Mindset`. Contains a one-line RFC 2119 key: *"The keywords MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY in this prompt follow RFC 2119."*
2. **`## Task`** — What the agent must do, step by step. Absorbs `## Process`, `## What You Check`, `## Before You Begin`, `## While You Work`, `## Self-Review`, `## What You DO / DO NOT Do`, `## Verification Before Completion`, `## Contexts`, `## Intent Categories`. Subsections within Task (e.g., `### Step 1: Dispatch Implementer`) are preserved as nested headings.
3. **`## Format`** — Output structure and delivery mechanism. Absorbs `## Output Format`, `## Report Format`, `## Plan Document Format`, `## Design Document Format`, `## Verdict`.

Additional domain-specific sections are permitted where they don't fit R/T/F:

- **`## Sub-Agents`** — Retained in orchestrator prompts (designing, planning, implementation) as a reference section between Role and Task.
- **`## Constraints`** — Replaces `## Rules` and `## Red Flags — Never Do These`. All items use RFC 2119 keywords with clear severity levels. When merging Red Flags and Rules (as in `implementation.py` where they partially overlap), duplicate entries are deduplicated.

### What Is NOT Changed

- **User prompts** (`build_*_user_prompt` functions) — These are data-carrying templates that inject issue/plan/design content. R/T/F structure does not apply to them.
- **Runtime wiring** — `agent.py` calls prompt functions identically; function signatures are unchanged.
- **Prompt semantics** — The actual instructions remain the same; only structure and keyword formality change.

### Semantic Preservation Note

Replacing informal imperatives ("Do NOT", "Never", "Always") with RFC 2119 keywords is not always a neutral transformation. Motivational and adversarial framing text — such as the `## Mindset` section in `spec_reviewer_prompt` ("The implementer finished suspiciously quickly. They probably cut corners...") — MUST be kept as informal prose, not promoted to MUST/MUST NOT statements. These passages establish behavioral tone, not procedural requirements, and converting them would change the agent's register from adversarial to bureaucratic.

Each informal imperative being promoted to a RFC 2119 keyword MUST be reviewed individually for semantic equivalence. The conversion is intentional and selective, not mechanical.

### RFC 2119 Keyword Application Strategy

Keywords are applied selectively based on severity:

- **MUST / MUST NOT** — Absolute requirements/prohibitions. Examples: "MUST NOT skip the Q&A phase", "MUST run tests before reporting completion", "MUST NOT parallelize implementer subagents".
- **SHOULD / SHOULD NOT** — Strong recommendations with valid exceptions. Examples: "SHOULD quote relevant issue text when answering", "SHOULD NOT flag issues in unchanged code".
- **MAY** — Truly optional behavior. Examples: "MAY include minor suggestions when approving".

Informal language is preserved for descriptive, advisory, and motivational text that doesn't express a requirement level.

### Canonical Section Order

All restructured prompts follow this order:

```
## Role          (mandatory — identity, purpose, RFC 2119 key)
## Sub-Agents    (orchestrator prompts only — reference listing)
## Task          (mandatory — instructions, process, checks)
## Format        (mandatory — output structure)
## Constraints   (present when there are hard rules — RFC 2119 items)
```

## Components

### 1. Orchestrator System Prompts

Four functions across three files, each restructured to R/T/F format.

**`build_designing_system_prompt()`** in `src/remote_agent/prompts/designing.py`

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph ("You are an expert software architect...") | `## Role` | Combined with RFC 2119 key |
| `## Sub-Agents` | `## Sub-Agents` | Unchanged, stays between Role and Task |
| `## Process` | `## Task` | Steps 1-5 preserved; imperatives converted to RFC 2119 |
| `## Design Document Format` | `## Format` | Markdown template preserved verbatim |
| `## Rules` | `## Constraints` | Each rule becomes MUST/MUST NOT/SHOULD |

Signature unchanged: `() -> str`

**`build_planning_system_prompt()`** in `src/remote_agent/prompts/planning.py`

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph | `## Role` | |
| `## Sub-Agents` | `## Sub-Agents` | Unchanged |
| `## Task Granularity` | `## Task` | Granularity guidelines use SHOULD (strong recommendation, not absolute) |
| `## Plan Document Format` | `## Format` | Markdown template preserved verbatim |
| `## Internal Review Loop` | `## Task` (subsection) | Absorbed as `### Review Loop` under Task |
| `## Rules` | `## Constraints` | MUST/MUST NOT keywords |

Signature unchanged: `() -> str`

**`build_implementation_system_prompt()`** in `src/remote_agent/prompts/implementation.py`

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph ("You are a senior developer...") | `## Role` | |
| `## Your Role` | `## Role` | Merged with opening paragraph |
| `## Available Sub-Agents` | `## Sub-Agents` | Renamed for consistency |
| `## Process` (Steps 1-5) | `## Task` | `### Step` subsections preserved |
| `## Verification Before Completion` | `## Task` (subsection) | Absorbed as `### Verification` under Task |
| `## Red Flags — Never Do These` | `## Constraints` | Deduplicated with Rules; items become MUST NOT |
| `## Rules` | `## Constraints` | Merged with Red Flags; duplicates removed |

**Deduplication guidance for Red Flags / Rules merge:** Three overlapping pairs exist. When merging, use the canonical MUST/MUST NOT form listed below:

| Red Flags Wording | Rules Wording | Canonical Merged Form |
|---|---|---|
| "Never parallelize implementers — execute tasks sequentially, one at a time" | "Execute tasks in order. Do not parallelize implementer subagents." | "MUST NOT parallelize implementer subagents — execute tasks sequentially, one at a time." |
| "Never skip reviews — every task gets both spec and code quality review" | "Do not skip reviews." | "MUST NOT skip reviews — every task gets both spec and code quality review." |
| "Always do spec review BEFORE code quality review" | "Always do spec review BEFORE code quality review." | "MUST perform spec review BEFORE code quality review." |

The `<commit_message>` XML tag instruction (currently at the end of Rules) moves to `## Format` since it describes output structure.

Signature unchanged: `() -> str`

**`build_review_system_prompt()`** in `src/remote_agent/prompts/review.py`

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph | `## Role` | |
| `## Your Task` | `## Task` | |
| `## Contexts` | `## Task` (subsection) | Absorbed as `### Contexts` — domain-specific reference within task instructions |
| `## Intent Categories` | `## Task` (subsection) | Absorbed as `### Intent Categories` — these are the classification definitions the agent uses |
| `## Rules` | `## Constraints` | |

`## Format` is new for this prompt — the instruction "Call the classify_comment tool with your classification" currently lives as the last line of `## Rules`. It is extracted and placed in the new `## Format` section, which specifies the tool-call output mechanism. This is the one prompt where `## Format` has no pre-existing section to absorb from.

**Note on duplication with user prompt:** The same "Call the classify_comment tool" instruction also appears as the final line of `build_review_user_prompt()`. After this restructure, the instruction will exist in both the system prompt `## Format` and the user prompt. This duplication is intentional — the user prompt is out of scope and remains unchanged, and reinforcing the tool-call instruction in both locations is harmless.

Signature unchanged: `() -> str`

### 2. Sub-Agent Prompts

Eight functions in `src/remote_agent/prompts/subagents.py`, each restructured to R/T/F.

**`codebase_explorer_prompt()`**

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph | `## Role` | |
| `## Your Focus Areas` (4 items) | `## Task` | Items preserved as numbered list |
| `## Output Format` | `## Format` | |
| `## Rules` | `## Constraints` | |

**`issue_advocate_prompt(issue_body: str)`**

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph | `## Role` | |
| `## The Issue` | `## Task` — placed as a `### Context` block at the start of Task | The `---`-delimited block wrapping `{issue_body}` is preserved verbatim. It appears before the response instructions, not after. |
| `## How to Respond` (3 items) | `## Task` | Follows the Context block. Note: item 3 already contains an RFC 2119 keyword ("you MUST clearly flag it") — this is preserved as-is. |
| `## Rules` | `## Constraints` | |

No explicit `## Format` — the response format is inline within Task (answer style, flagging inferences). A thin `## Format` section is added stating the expected response structure (direct answer with evidence, inference flagging).

Signature unchanged: `(issue_body: str) -> str`

**`design_critic_prompt()`**

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph | `## Role` | |
| `## What You Check` (4 items) | `## Task` | |
| `## Output Format` (Approve/Revise/Reject) | `## Format` | |
| `## Rules` | `## Constraints` | |

**`plan_reviewer_prompt()`**

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph | `## Role` | |
| `## What You Check` (6 items) | `## Task` | |
| `## Output Format` (Approve/Reject) | `## Format` | |
| `## Rules` | `## Constraints` | |

**`implementer_prompt()`**

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph | `## Role` | |
| `## Before You Begin` | `## Task` → `### Before You Begin` | Preserved as subsection |
| `## While You Work` | `## Task` → `### While You Work` | Advisory bullets stay informal; hard prohibitions ("PAUSE and ask for clarification") become MUST |
| `## Self-Review` (4 categories) | `## Task` → `### Self-Review` | Preserved as subsection with its `####` sub-items |
| `## Report Format` | `## Format` | |

Specific bullets moving to `## Constraints`:
- "Make the smallest change that satisfies the task requirements" → MUST (from While You Work)
- "Do not report known issues and hope the reviewer will not notice" → MUST NOT (from Self-Review)
- "Write tests alongside implementation, not as an afterthought" → MUST (from While You Work)

Remaining While You Work bullets are advisory and stay in `## Task`.

**`spec_reviewer_prompt()`**

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph | `## Role` | |
| `## Mindset` | `## Role` | Merged into Role as behavioral stance. The adversarial framing ("finished suspiciously quickly", "probably cut corners") is preserved as **informal prose**, not converted to RFC 2119 keywords. |
| `## What You DO` | `## Task` | |
| `## What You DO NOT Do` | `## Constraints` | These are prohibitions → MUST NOT |
| `## Report Format` + `## Verdict` | `## Format` | Combined into single Format section |
| `## Rules` | `## Constraints` | Merged with "What You DO NOT Do" |

**`code_quality_reviewer_prompt()`**

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph | `## Role` | |
| `## What You Check` (5 items) | `## Task` | |
| `## Report Format` (Strengths/Issues/Assessment) | `## Format` | |
| `## Rules` | `## Constraints` | |

**`final_reviewer_prompt()`**

| Current Section | New Location | Notes |
|---|---|---|
| Opening paragraph + `## Your Purpose` | `## Role` | Merged |
| `## What You Check` (5 items) | `## Task` | |
| `## Report Format` (4 subsections + Verdict) | `## Format` | |
| `## Rules` | `## Constraints` | |

### 3. User Prompts (No Changes)

Four user prompt functions are excluded from restructuring:

- `build_designing_user_prompt(...)` in `designing.py`
- `build_planning_user_prompt(...)` in `planning.py`
- `build_implementation_user_prompt(...)` in `implementation.py`
- `build_review_user_prompt(...)` in `review.py`

These are data-carrying templates. Their function signatures, content assembly logic, and output remain unchanged.

### 4. Package Init (No Changes)

`src/remote_agent/prompts/__init__.py` is empty (no re-exports). Unaffected by this change.

### 5. Test Updates

`tests/test_prompts.py` requires updates in three categories:

**a) New structural assertions** — Add tests verifying every system/sub-agent prompt contains:
- `"## Role"` section header
- `"## Task"` section header
- `"## Format"` section header
- At least one RFC 2119 keyword (`MUST`, `SHOULD`, or `MAY`)

**b) Broken assertions to update** — The following existing tests will break due to structural changes:

| Test | Current Assertion | Why It Breaks | Fix |
|---|---|---|---|
| `test_implementer_prompt_has_before_you_begin` | `"Before You Begin" in prompt` | Header becomes `### Before You Begin` under Task — likely still matches, but verify |
| `test_implementer_prompt_has_self_review` | `"Self-Review" in prompt` | Header becomes `### Self-Review` under Task — likely still matches, but verify |
| `test_implementation_system_prompt_red_flags` | `"never parallelize" in prompt.lower()` | "Never parallelize" becomes "MUST NOT parallelize" | Change to `"must not parallelize" in prompt.lower()` |
| `test_implementation_system_prompt_red_flags` | `"never skip review" in prompt.lower()` | "Never skip reviews" becomes "MUST NOT skip reviews" | Change to `"must not skip review" in prompt.lower()` |
| `test_implementation_system_prompt_red_flags` | `"scene-setting" in prompt.lower()` | Text survives restructure (lives inside Step 1 instructions) — no change expected |
| `test_implementation_system_prompt_red_flags` | `"full task text" in prompt.lower()` | Text survives restructure (lives inside Step 1 context instructions in `## Task`) — no change expected |
| `test_implementation_system_prompt_red_flags` | `"3 iteration" in prompt.lower()` | Text survives restructure (review loop iteration limit in `## Task` or `## Constraints`) — no change expected |
| `test_spec_reviewer_prompt_adversarial` | `"Do NOT trust" in prompt` | Adversarial text preserved as informal prose per Semantic Preservation Note — no change expected |

**c) Preserved assertions** — All remaining content-based assertions (checking for sub-agent names, keywords like "YAGNI", "architect", "codebase-explorer", etc.) remain valid since semantic content is preserved.

## Data Flow

This change has no data flow impact. The data flow through the prompt system is:

1. **Phase handler** (e.g., `DesigningHandler.handle()`) calls `AgentService.run_designing()`
2. `AgentService` calls `build_designing_system_prompt()` and `build_designing_user_prompt(...)` to get prompt strings
3. Prompt strings are passed to `ClaudeAgentOptions(system_prompt=...)` and `query(prompt=...)`
4. Sub-agent prompts are assembled by `_get_designing_subagents()` into `AgentDefinition(prompt=...)` objects

All function signatures are unchanged. All call sites in `agent.py` are unchanged. The only difference is the string content returned by prompt functions.

## Error Handling

No new error modes are introduced. The only risk is prompt regression — a restructured prompt that accidentally changes agent behavior. This is mitigated by:

1. **Test coverage** — Existing content assertions catch accidental removal of key instructions
2. **Structural tests** — New assertions verify R/T/F sections and RFC 2119 keywords are present
3. **Semantic preservation review** — Each RFC 2119 keyword conversion is reviewed individually (not mechanically converted)

If a restructured prompt causes a behavioral regression in production, the fix is to revert the specific prompt function to its previous content. Each prompt function is independent — reverting one does not affect others.

## Testing Strategy

### Unit Tests (`tests/test_prompts.py`)

**New tests to add:**

```python
# Structural: verify every system/sub-agent prompt has R/T/F sections
@pytest.mark.parametrize("prompt_fn", [
    build_designing_system_prompt,
    build_planning_system_prompt,
    build_implementation_system_prompt,
    build_review_system_prompt,
    codebase_explorer_prompt,
    design_critic_prompt,
    plan_reviewer_prompt,
    implementer_prompt,
    spec_reviewer_prompt,
    code_quality_reviewer_prompt,
    final_reviewer_prompt,
])
def test_prompt_has_role_task_format_sections(prompt_fn):
    prompt = prompt_fn()
    assert "## Role" in prompt
    assert "## Task" in prompt
    assert "## Format" in prompt

def test_issue_advocate_prompt_has_role_task_format():
    prompt = issue_advocate_prompt("test issue")
    assert "## Role" in prompt
    assert "## Task" in prompt
    assert "## Format" in prompt

def test_issue_advocate_prompt_uses_rfc2119_keywords():
    prompt = issue_advocate_prompt("test issue")
    has_keyword = any(kw in prompt for kw in ["MUST", "SHOULD", "MAY"])
    assert has_keyword

# RFC 2119: verify keyword presence
@pytest.mark.parametrize("prompt_fn", [
    build_designing_system_prompt,
    build_planning_system_prompt,
    build_implementation_system_prompt,
    build_review_system_prompt,
    codebase_explorer_prompt,
    design_critic_prompt,
    plan_reviewer_prompt,
    implementer_prompt,
    spec_reviewer_prompt,
    code_quality_reviewer_prompt,
    final_reviewer_prompt,
])
def test_prompt_uses_rfc2119_keywords(prompt_fn):
    prompt = prompt_fn()
    has_keyword = any(kw in prompt for kw in ["MUST", "SHOULD", "MAY"])
    assert has_keyword, f"{prompt_fn.__name__} has no RFC 2119 keywords"
```

**Tests to update:**

- `test_implementation_system_prompt_red_flags`: Change `"never parallelize"` to `"must not parallelize"` and `"never skip review"` to `"must not skip review"` (case-insensitive assertions already used)

**Tests to preserve unchanged:**

- All sub-agent name assertions (`"codebase-explorer"`, `"issue-advocate"`, etc.)
- All content keyword assertions (`"YAGNI"`, `"architect"`, `"classify_comment"`, etc.)
- All user prompt tests (user prompts are unchanged)

### Edge Cases

- `issue_advocate_prompt(issue_body)` — Verify the `{issue_body}` f-string injection still works correctly after restructure
- `build_review_system_prompt()` — Verify the new `## Format` section (which has no predecessor) contains the classify_comment tool instruction
- `spec_reviewer_prompt()` — Verify adversarial framing in `## Role` remains informal (not converted to MUST NOT)
- Orchestrator prompts with `<commit_message>` instructions — Verify the XML tag instruction appears in `## Format`
