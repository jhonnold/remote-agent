# Prompt Flow Alignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure the remote agent's prompt flow to match the superpowers brainstorming/planning/implementation skill chain, replacing human interaction points with sub-agent directives.

**Architecture:** New `designing` and `design_review` phases added before planning. Plan review human gate removed. Each phase gets clean context with explicit inputs. Sub-agents replace every human interaction point except two GitHub gates (design_review on the issue, code_review on the PR).

**Tech Stack:** Python 3.11+, aiosqlite, claude-agent-sdk, pytest, pytest-asyncio

**Design:** `docs/plans/2026-03-27-prompt-flow-alignment-design.md`

---

### Task 1: Update Issue Model — Rename Fields and Add plan_path

**Files:**
- Modify: `src/remote_agent/models.py:7-27`
- Test: `tests/test_models.py`

**Step 1: Write the failing test**

Add tests to `tests/test_models.py` verifying the new field names exist on Issue:

```python
from remote_agent.models import Issue

def test_issue_has_design_approved_field():
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="t", body="b", phase="new", design_approved=True)
    assert issue.design_approved is True

def test_issue_has_design_commit_hash_field():
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="t", body="b", phase="new", design_commit_hash="abc123")
    assert issue.design_commit_hash == "abc123"

def test_issue_has_plan_path_field():
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="t", body="b", phase="new", plan_path="/tmp/plan.md")
    assert issue.plan_path == "/tmp/plan.md"

def test_issue_design_fields_default_to_none_or_false():
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="t", body="b", phase="new")
    assert issue.design_approved is False
    assert issue.design_commit_hash is None
    assert issue.plan_path is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'design_approved'`

**Step 3: Implement minimal code**

In `src/remote_agent/models.py`, rename fields on the `Issue` dataclass:
- `plan_approved: bool = False` → `design_approved: bool = False`
- `plan_commit_hash: str | None = None` → `design_commit_hash: str | None = None`
- Add: `plan_path: str | None = None`

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/models.py tests/test_models.py
git commit -m "refactor: rename plan fields to design on Issue model, add plan_path"
```

---

### Task 2: Update Database Schema and Methods

**Files:**
- Modify: `src/remote_agent/db.py:13-75` (SCHEMA), `src/remote_agent/db.py:184-198` (methods), `src/remote_agent/db.py:232-241` (clear_issue_for_reopen), `src/remote_agent/db.py:386-399` (_row_to_issue)
- Test: `tests/test_db.py`

**Step 1: Write the failing tests**

Add to `tests/test_db.py`:

```python
async def test_set_design_approved(db, sample_issue_data):
    issue_id = await db.create_issue("o", "r", sample_issue_data)
    await db.set_design_approved(issue_id, True)
    issue = await db.get_issue("o", "r", sample_issue_data["number"])
    assert issue.design_approved is True

async def test_set_design_commit_hash(db, sample_issue_data):
    issue_id = await db.create_issue("o", "r", sample_issue_data)
    await db.set_design_commit_hash(issue_id, "abc123")
    issue = await db.get_issue("o", "r", sample_issue_data["number"])
    assert issue.design_commit_hash == "abc123"

async def test_set_plan_path(db, sample_issue_data):
    issue_id = await db.create_issue("o", "r", sample_issue_data)
    await db.set_plan_path(issue_id, "/tmp/.plans/issue-42-plan.md")
    issue = await db.get_issue("o", "r", sample_issue_data["number"])
    assert issue.plan_path == "/tmp/.plans/issue-42-plan.md"

async def test_clear_plan_path(db, sample_issue_data):
    issue_id = await db.create_issue("o", "r", sample_issue_data)
    await db.set_plan_path(issue_id, "/tmp/plan.md")
    await db.clear_plan_path(issue_id)
    issue = await db.get_issue("o", "r", sample_issue_data["number"])
    assert issue.plan_path is None

async def test_get_issues_awaiting_comment_includes_design_review(db, sample_issue_data):
    issue_id = await db.create_issue("o", "r", sample_issue_data)
    await db.update_issue_phase(issue_id, "design_review")
    issues = await db.get_issues_awaiting_comment("o", "r")
    assert len(issues) == 1
    assert issues[0].phase == "design_review"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py::test_set_design_approved tests/test_db.py::test_set_design_commit_hash tests/test_db.py::test_set_plan_path tests/test_db.py::test_clear_plan_path tests/test_db.py::test_get_issues_awaiting_comment_includes_design_review -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'set_design_approved'`

**Step 3: Implement minimal code**

1. In SCHEMA (line 13-75): rename `plan_approved` → `design_approved`, `plan_commit_hash` → `design_commit_hash`, add `plan_path TEXT` column to the issues table.

2. Add migration blocks in `initialize()` (after line 104):
```python
try:
    await conn.execute("ALTER TABLE issues ADD COLUMN design_approved INTEGER DEFAULT 0")
    await conn.commit()
except Exception:
    pass
try:
    await conn.execute("ALTER TABLE issues ADD COLUMN design_commit_hash TEXT")
    await conn.commit()
except Exception:
    pass
try:
    await conn.execute("ALTER TABLE issues ADD COLUMN plan_path TEXT")
    await conn.commit()
except Exception:
    pass
# Migrate data from old columns if they exist
try:
    await conn.execute("UPDATE issues SET design_approved = plan_approved WHERE design_approved = 0 AND plan_approved = 1")
    await conn.commit()
except Exception:
    pass
try:
    await conn.execute("UPDATE issues SET design_commit_hash = plan_commit_hash WHERE design_commit_hash IS NULL AND plan_commit_hash IS NOT NULL")
    await conn.commit()
except Exception:
    pass
```

3. Rename methods:
   - `set_plan_approved` → `set_design_approved` (update SQL to use `design_approved`)
   - `set_plan_commit_hash` → `set_design_commit_hash` (update SQL to use `design_commit_hash`)

4. Add new methods:
```python
async def set_plan_path(self, issue_id: int, plan_path: str):
    await self._conn.execute(
        "UPDATE issues SET plan_path = ?, updated_at = datetime('now') WHERE id = ?",
        (plan_path, issue_id),
    )
    await self._conn.commit()

async def clear_plan_path(self, issue_id: int):
    await self._conn.execute(
        "UPDATE issues SET plan_path = NULL, updated_at = datetime('now') WHERE id = ?",
        (issue_id,),
    )
    await self._conn.commit()
```

5. Update `get_issues_awaiting_comment` (line 139) — change the phase IN clause:
   `"phase IN ('plan_review', 'code_review', 'error')"` → `"phase IN ('design_review', 'code_review', 'error')"`

6. Update `get_active_issues` (line 147) — add `'designing'` and `'planning'` to the IN clause:
   `"phase IN ('planning', 'implementing')"` → `"phase IN ('designing', 'planning', 'implementing')"`

7. Update `clear_issue_for_reopen` (line 233-241) — rename `plan_commit_hash` → `design_commit_hash` in SQL.

8. Update `_row_to_issue` (line 386-399) — map new column names. Use `.get()` or try/except for backwards compatibility with old column names during migration:
```python
@staticmethod
def _row_to_issue(row) -> Issue:
    return Issue(
        id=row["id"], repo_owner=row["repo_owner"], repo_name=row["repo_name"],
        issue_number=row["issue_number"], title=row["title"], body=row["body"],
        phase=row["phase"], branch_name=row["branch_name"], pr_number=row["pr_number"],
        workspace_path=row["workspace_path"],
        design_approved=bool(row["design_approved"]),
        design_commit_hash=row["design_commit_hash"],
        plan_path=row["plan_path"],
        last_comment_id=row["last_comment_id"],
        last_review_id=row["last_review_id"],
        issue_closed_seen=bool(row["issue_closed_seen"]),
        last_issue_comment_id=row["last_issue_comment_id"],
        budget_notified=bool(row["budget_notified"]), error_message=row["error_message"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/db.py tests/test_db.py
git commit -m "refactor: rename plan DB fields to design, add plan_path column"
```

---

### Task 3: Update Config — Change orchestrator_model Default

**Files:**
- Modify: `src/remote_agent/config.py:48`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_agent_config_default_orchestrator_model_is_sonnet():
    from remote_agent.config import AgentConfig
    config = AgentConfig()
    assert config.orchestrator_model == "sonnet"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_agent_config_default_orchestrator_model_is_sonnet -v`
Expected: FAIL — `AssertionError: assert 'haiku' == 'sonnet'`

**Step 3: Implement minimal code**

In `src/remote_agent/config.py` line 48, change:
```python
orchestrator_model: str = "haiku"
```
to:
```python
orchestrator_model: str = "sonnet"
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/config.py tests/test_config.py
git commit -m "chore: change orchestrator_model default from haiku to sonnet"
```

---

### Task 4: Create Sub-Agent Prompts Module

**Files:**
- Create: `src/remote_agent/prompts/subagents.py`
- Test: `tests/test_prompts.py` (add tests)

**Step 1: Write the failing tests**

Add to `tests/test_prompts.py`:

```python
from remote_agent.prompts.subagents import (
    codebase_explorer_prompt,
    issue_advocate_prompt,
    design_critic_prompt,
    plan_reviewer_prompt,
    implementer_prompt,
    spec_reviewer_prompt,
    code_quality_reviewer_prompt,
    final_reviewer_prompt,
)

def test_codebase_explorer_prompt():
    prompt = codebase_explorer_prompt()
    assert "codebase" in prompt.lower()
    assert "structure" in prompt.lower()

def test_issue_advocate_prompt_includes_issue_body():
    prompt = issue_advocate_prompt("We need OAuth2 support")
    assert "We need OAuth2 support" in prompt
    assert "issue" in prompt.lower()
    assert "codebase" in prompt.lower()

def test_issue_advocate_prompt_flags_inferences():
    prompt = issue_advocate_prompt("Add auth")
    assert "infer" in prompt.lower() or "flag" in prompt.lower()

def test_design_critic_prompt():
    prompt = design_critic_prompt()
    assert "design" in prompt.lower()
    assert "YAGNI" in prompt or "yagni" in prompt.lower()

def test_plan_reviewer_prompt():
    prompt = plan_reviewer_prompt()
    assert "plan" in prompt.lower()
    assert "design" in prompt.lower()

def test_implementer_prompt_has_before_you_begin():
    prompt = implementer_prompt()
    assert "Before You Begin" in prompt or "before you begin" in prompt.lower()

def test_implementer_prompt_has_self_review():
    prompt = implementer_prompt()
    assert "self-review" in prompt.lower() or "Self-Review" in prompt
    assert "Completeness" in prompt
    assert "Quality" in prompt
    assert "Discipline" in prompt
    assert "Testing" in prompt

def test_spec_reviewer_prompt_adversarial():
    prompt = spec_reviewer_prompt()
    assert "Do NOT trust" in prompt or "do not trust" in prompt.lower()
    assert "suspiciously" in prompt.lower() or "verify independently" in prompt.lower()

def test_code_quality_reviewer_prompt():
    prompt = code_quality_reviewer_prompt()
    assert "Critical" in prompt
    assert "Important" in prompt
    assert "Minor" in prompt

def test_final_reviewer_prompt():
    prompt = final_reviewer_prompt()
    assert "holistic" in prompt.lower() or "entire" in prompt.lower()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prompts.py::test_codebase_explorer_prompt -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'remote_agent.prompts.subagents'`

**Step 3: Implement minimal code**

Create `src/remote_agent/prompts/subagents.py` with all sub-agent prompt functions. Each function returns a string containing the full system prompt for that sub-agent role.

Key prompts to implement (reference the superpowers skill templates in the design doc):

- `codebase_explorer_prompt()` — Exploration specialist. Analyze code structure, patterns, conventions. Focus on project structure, testing patterns, key abstractions, coding style.

- `issue_advocate_prompt(issue_body: str)` — Parameterized with the issue body baked in. Role: proxy for the issue author. Answer from the issue first. Supplement with codebase evidence. Explicitly flag when inferring beyond what the issue states.

- `design_critic_prompt()` — Reviews design sections for completeness, feasibility, alignment with issue goals, YAGNI violations. Can reject sections and request revision.

- `plan_reviewer_prompt()` — Reviews plan against design doc. Checks: every design requirement has a task, correct ordering, valid file paths, correct test commands, no missing/extra work (YAGNI). Can reject.

- `implementer_prompt()` — Matches superpowers `implementer-prompt.md`. Includes "Before You Begin" section (ask questions), "While you work" (pause and clarify), detailed 4-category self-review (Completeness, Quality, Discipline, Testing), "fix issues before reporting", structured report format.

- `spec_reviewer_prompt()` — Matches superpowers `spec-reviewer-prompt.md`. Adversarial framing ("finished suspiciously quickly"). Explicit DO/DON'T. Structured: Missing requirements, Extra/unneeded work, Misunderstandings. Verify by reading code.

- `code_quality_reviewer_prompt()` — Reviews code quality after spec passes. Reports: Strengths, Issues (Critical/Important/Minor with file:line), Assessment.

- `final_reviewer_prompt()` — Holistic review of entire implementation. Cross-task integration, architecture coherence, full test suite review.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_prompts.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/prompts/subagents.py tests/test_prompts.py
git commit -m "feat: add centralized sub-agent prompt definitions"
```

---

### Task 5: Create Designing Phase Prompts

**Files:**
- Create: `src/remote_agent/prompts/designing.py`
- Test: `tests/test_prompts.py` (add tests)

**Step 1: Write the failing tests**

Add to `tests/test_prompts.py`:

```python
from remote_agent.prompts.designing import build_designing_system_prompt, build_designing_user_prompt

def test_designing_system_prompt_contains_key_instructions():
    prompt = build_designing_system_prompt()
    assert "architect" in prompt.lower()
    assert "codebase-explorer" in prompt or "codebase_explorer" in prompt
    assert "issue-advocate" in prompt or "issue_advocate" in prompt
    assert "design-critic" in prompt or "design_critic" in prompt
    assert "2-3 approaches" in prompt or "two to three" in prompt.lower()

def test_designing_user_prompt_new_issue():
    prompt = build_designing_user_prompt(
        issue_number=42, issue_title="Add auth", issue_body="Need OAuth2",
    )
    assert "#42" in prompt
    assert "Add auth" in prompt
    assert "Need OAuth2" in prompt
    assert "issue-42-design.md" in prompt

def test_designing_user_prompt_revision():
    prompt = build_designing_user_prompt(
        issue_number=42, issue_title="Add auth", issue_body="Need OAuth2",
        existing_design="## Old design", feedback="Change the approach",
    )
    assert "Change the approach" in prompt
    assert "Old design" in prompt
    assert "Revision" in prompt or "revision" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prompts.py::test_designing_system_prompt_contains_key_instructions -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement minimal code**

Create `src/remote_agent/prompts/designing.py` with two functions:

`build_designing_system_prompt()` — System prompt for the design orchestrator. Must include:
- Role: expert software architect brainstorming through dialogue
- Process: 1) Explore via codebase-explorer, 2) Clarify via issue-advocate one at a time, 3) Propose 2-3 approaches to issue-advocate, 4) Present sections to design-critic, 5) Write design doc
- Sub-agent names and when to use each
- Must NOT skip Q&A even for simple issues
- Design doc format and file path convention

`build_designing_user_prompt(issue_number, issue_title, issue_body, existing_design=None, feedback=None)` — User prompt. Includes issue details, output path `docs/plans/issue-{N}-design.md`, and revision context if applicable.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_prompts.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/prompts/designing.py tests/test_prompts.py
git commit -m "feat: add designing phase prompt builders"
```

---

### Task 6: Rewrite Planning Phase Prompts

**Files:**
- Modify: `src/remote_agent/prompts/planning.py:1-79`
- Test: `tests/test_prompts.py` (update existing tests)

**Step 1: Write the failing tests**

Update the existing planning prompt tests in `tests/test_prompts.py` and add new ones:

```python
def test_planning_system_prompt_references_design_doc():
    prompt = build_planning_system_prompt()
    assert "design" in prompt.lower()
    assert "plan-reviewer" in prompt or "plan_reviewer" in prompt
    assert "bite-sized" in prompt.lower() or "single action" in prompt.lower()

def test_planning_user_prompt_includes_design_content():
    prompt = build_planning_user_prompt(
        issue_number=42, issue_title="Add auth", issue_body="Need OAuth2",
        design_content="## Design\nUse token-based auth",
    )
    assert "Use token-based auth" in prompt
    assert "#42" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prompts.py::test_planning_system_prompt_references_design_doc tests/test_prompts.py::test_planning_user_prompt_includes_design_content -v`
Expected: FAIL — new test assertions fail

**Step 3: Implement minimal code**

Rewrite `src/remote_agent/prompts/planning.py`:

- `build_planning_system_prompt()` — Updated to reference design doc as input, include plan-reviewer sub-agent, specify superpowers-level task granularity (each step = one action), include exact plan format from design doc.

- `build_planning_user_prompt(issue_number, issue_title, issue_body, design_content)` — New signature adds `design_content` parameter. Remove `existing_plan` and `feedback` params (plan is now internal, no revision from human). Include both issue details and design content in the prompt.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_prompts.py -v`
Expected: PASS (update any broken existing tests to match new signatures)

**Step 5: Commit**

```bash
git add src/remote_agent/prompts/planning.py tests/test_prompts.py
git commit -m "refactor: rewrite planning prompts to take design doc as input"
```

---

### Task 7: Enhance Implementation Phase Prompts

**Files:**
- Modify: `src/remote_agent/prompts/implementation.py:1-60`
- Test: `tests/test_prompts.py` (update existing tests)

**Step 1: Write the failing tests**

Update and add tests in `tests/test_prompts.py`:

```python
def test_implementation_system_prompt_references_issue_advocate():
    prompt = build_implementation_system_prompt()
    assert "issue-advocate" in prompt or "issue_advocate" in prompt
    assert "final-reviewer" in prompt or "final_reviewer" in prompt
    assert "scene-setting" in prompt.lower() or "scene setting" in prompt.lower()

def test_implementation_user_prompt_includes_design_and_issue():
    prompt = build_implementation_user_prompt(
        plan_content="## Plan", issue_title="Add auth",
        issue_body="Need OAuth2", design_content="## Design",
    )
    assert "## Plan" in prompt
    assert "## Design" in prompt
    assert "Need OAuth2" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prompts.py::test_implementation_system_prompt_references_issue_advocate tests/test_prompts.py::test_implementation_user_prompt_includes_design_and_issue -v`
Expected: FAIL

**Step 3: Implement minimal code**

Enhance `src/remote_agent/prompts/implementation.py`:

- `build_implementation_system_prompt()` — Add references to `issue-advocate` for answering implementer questions, `final-reviewer` for holistic review after all tasks, scene-setting context requirement, verification-before-completion (full test suite). Keep existing orchestrator rules (sequential, spec before quality, max 3 iterations). Add superpowers red flags list.

- `build_implementation_user_prompt(plan_content, issue_title, issue_body, design_content, feedback=None)` — New signature adds `issue_body` and `design_content`. Include all three context documents (plan, design, issue) in the user prompt.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_prompts.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/prompts/implementation.py tests/test_prompts.py
git commit -m "feat: enhance implementation prompts with issue-advocate and final-reviewer"
```

---

### Task 8: Activate Review Prompts for AI-Powered Classification

**Files:**
- Modify: `src/remote_agent/prompts/review.py:1-47`
- Test: `tests/test_prompts.py` (update existing tests)

**Step 1: Write the failing tests**

Update tests in `tests/test_prompts.py`:

```python
def test_review_system_prompt_supports_design_review_context():
    prompt = build_review_system_prompt()
    assert "design_review" in prompt or "design review" in prompt.lower()

def test_review_user_prompt_design_review():
    prompt = build_review_user_prompt(
        comment="Looks good", context="design_review",
        issue_title="Add auth",
    )
    assert "design_review" in prompt
    assert "approve" in prompt

def test_review_user_prompt_design_review_valid_intents():
    prompt = build_review_user_prompt(
        comment="test", context="design_review", issue_title="t",
    )
    assert "approve" in prompt
    assert "revise" in prompt
    assert "question" in prompt
    assert "back_to_design" not in prompt

def test_review_user_prompt_code_review_includes_back_to_design():
    prompt = build_review_user_prompt(
        comment="test", context="code_review", issue_title="t",
    )
    assert "back_to_design" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prompts.py::test_review_system_prompt_supports_design_review_context tests/test_prompts.py::test_review_user_prompt_design_review -v`
Expected: FAIL

**Step 3: Implement minimal code**

Update `src/remote_agent/prompts/review.py`:

- `build_review_system_prompt()` — Add `design_review` as a valid context. Update `back_to_planning` intent to `back_to_design` throughout.

- `build_review_user_prompt()` — Add `design_review` context handling with valid intents `approve, revise, question`. Update `code_review` context to use `back_to_design` instead of `back_to_planning`.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_prompts.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/prompts/review.py tests/test_prompts.py
git commit -m "feat: activate review prompts with design_review context and back_to_design intent"
```

---

### Task 9: Update AgentService — New Methods and Updated Signatures

**Files:**
- Modify: `src/remote_agent/agent.py:1-268`
- Test: `tests/test_agent.py`

**Step 1: Write the failing tests**

Add to `tests/test_agent.py`:

```python
def test_agent_service_has_run_designing_method(agent_service):
    assert hasattr(agent_service, 'run_designing')
    assert callable(agent_service.run_designing)

def test_agent_service_has_answer_question_method(agent_service):
    assert hasattr(agent_service, 'answer_question')
    assert callable(agent_service.answer_question)

def test_get_designing_subagents(agent_service):
    subagents = agent_service._get_designing_subagents("Test issue body")
    assert "codebase-explorer" in subagents
    assert "issue-advocate" in subagents
    assert "design-critic" in subagents

def test_get_planning_subagents_updated(agent_service):
    subagents = agent_service._get_planning_subagents()
    assert "codebase-explorer" in subagents
    assert "plan-reviewer" in subagents

def test_get_implementation_subagents_updated(agent_service):
    subagents = agent_service._get_implementation_subagents("Issue body text")
    assert "implementer" in subagents
    assert "spec-reviewer" in subagents
    assert "code-reviewer" in subagents
    assert "issue-advocate" in subagents
    assert "final-reviewer" in subagents

def test_classify_back_to_design(agent_service):
    result = agent_service._classify_comment_text("let's rethink the design", "code_review")
    assert result.intent == "back_to_design"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent.py::test_agent_service_has_run_designing_method tests/test_agent.py::test_get_designing_subagents -v`
Expected: FAIL

**Step 3: Implement minimal code**

Major changes to `src/remote_agent/agent.py`:

1. Import new prompt builders:
```python
from remote_agent.prompts.designing import build_designing_system_prompt, build_designing_user_prompt
from remote_agent.prompts.review import build_review_system_prompt, build_review_user_prompt
from remote_agent.prompts.subagents import (
    codebase_explorer_prompt, issue_advocate_prompt, design_critic_prompt,
    plan_reviewer_prompt, implementer_prompt, spec_reviewer_prompt,
    code_quality_reviewer_prompt, final_reviewer_prompt,
)
```

2. Add `run_designing()` method — similar structure to `run_planning()`, uses `build_designing_system_prompt/user_prompt`, dispatches designing subagents, uses `planning_model`.

3. Update `run_planning()` signature — add `design_content` param, remove `existing_plan`/`feedback` params. Pass `design_content` to `build_planning_user_prompt`.

4. Update `run_implementation()` signature — add `design_content` and `issue_body` params. Pass to `build_implementation_user_prompt`.

5. Add `answer_question()` method — lightweight agent run with issue + design/plan context, returns string answer.

6. Update `interpret_comment()` — switch from regex-only to AI-powered classification using `build_review_system_prompt/user_prompt`. Keep regex as fallback. Add `design_content`/`plan_content` optional params.

7. Update `_get_planning_subagents()` — replace `codebase-explorer` with prompt from `subagents.py`, add `plan-reviewer` subagent.

8. Add `_get_designing_subagents(issue_body: str)` — parameterized because `issue-advocate` needs the issue body. Returns dict with `codebase-explorer`, `issue-advocate`, `design-critic`.

9. Update `_get_implementation_subagents(issue_body: str)` — parameterized. Add `issue-advocate` and `final-reviewer`. Update existing subagent prompts to use `subagents.py`.

10. Update regex patterns: rename `_BACK_TO_PLANNING_RE` → `_BACK_TO_DESIGN_RE`, update pattern to match `back to design` and `rethink the design`.

11. Update `_classify_comment_text` — use `back_to_design` intent instead of `back_to_planning`.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent.py -v`
Expected: PASS (update any broken existing tests for renamed intents/signatures)

**Step 5: Commit**

```bash
git add src/remote_agent/agent.py tests/test_agent.py
git commit -m "feat: add run_designing, answer_question, update agent signatures and subagents"
```

---

### Task 10: Create Designing Phase Handler

**Files:**
- Create: `src/remote_agent/phases/designing.py`
- Create: `tests/test_phases/test_designing.py`

**Step 1: Write the failing tests**

Create `tests/test_phases/test_designing.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from remote_agent.phases.designing import DesigningHandler
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.agent import AgentResult


@pytest.fixture
def deps():
    return {
        "db": AsyncMock(),
        "github": AsyncMock(),
        "agent_service": AsyncMock(),
        "workspace_mgr": AsyncMock(),
    }

@pytest.fixture
def handler(deps):
    return DesigningHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])

@pytest.fixture
def new_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="Need OAuth2", phase="new")

@pytest.fixture
def new_issue_event():
    return Event(id=1, issue_id=1, event_type="new_issue",
                 payload={"number": 42, "title": "Add auth", "body": "Need OAuth2"})


async def test_designing_creates_branch_and_posts_design(handler, deps, new_issue, new_issue_event):
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_designing.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with patch("pathlib.Path.exists", return_value=False):
        result = await handler.handle(new_issue, new_issue_event)

    assert result.next_phase == "design_review"
    deps["workspace_mgr"].ensure_branch.assert_called_once()
    deps["workspace_mgr"].commit_and_push.assert_called_once()
    deps["db"].set_design_commit_hash.assert_called_once_with(1, "abc123")
    # Posts design as issue comment (not PR comment)
    deps["github"].post_comment.assert_called_once()
    call_args = deps["github"].post_comment.call_args
    assert call_args[0][2] == 42  # issue_number, not pr_number


async def test_designing_revision_passes_feedback(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="designing",
                  branch_name="agent/issue-42")
    event = Event(id=2, issue_id=1, event_type="revision_requested",
                  payload={"body": "Change the approach"})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "def456"
    deps["agent_service"].run_designing.return_value = AgentResult(
        success=True, session_id="sess-2", cost_usd=0.5, input_tokens=50, output_tokens=100,
    )

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="## Old design"):
        result = await handler.handle(issue, event)

    assert result.next_phase == "design_review"
    # Verify feedback and existing design were passed to agent
    call_kwargs = deps["agent_service"].run_designing.call_args.kwargs
    assert call_kwargs["feedback"] == "Change the approach"
    assert call_kwargs["existing_design"] == "## Old design"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phases/test_designing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'remote_agent.phases.designing'`

**Step 3: Implement minimal code**

Create `src/remote_agent/phases/designing.py`:

```python
from __future__ import annotations
import logging
from pathlib import Path

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class DesigningHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        logger.info("Handling designing for issue %d", issue.id)
        workspace = await self.workspace_mgr.ensure_workspace(
            issue.repo_owner, issue.repo_name, issue.issue_number,
        )
        await self.db.update_issue_workspace(issue.id, workspace)

        branch = issue.branch_name or f"agent/issue-{issue.issue_number}"
        force = issue.branch_name is None
        await self.workspace_mgr.ensure_branch(workspace, branch, force=force)
        await self.db.update_issue_branch(issue.id, branch)

        # Read existing design if revision
        existing_design = None
        design_path = Path(workspace) / "docs" / "plans" / f"issue-{issue.issue_number}-design.md"
        if design_path.exists():
            existing_design = design_path.read_text()

        feedback = event.payload.get("body") if event.event_type in ("revision_requested", "new_comment") else None

        await self.agent_service.run_designing(
            issue_number=issue.issue_number,
            issue_title=issue.title,
            issue_body=issue.body or "",
            cwd=workspace,
            issue_id=issue.id,
            existing_design=existing_design,
            feedback=feedback,
        )

        commit_msg = "docs: design for issue #{}".format(issue.issue_number)
        if existing_design:
            commit_msg = "docs: revise design for issue #{}".format(issue.issue_number)
        await self.workspace_mgr.commit_and_push(workspace, branch, commit_msg)

        design_commit = await self.workspace_mgr.get_head_commit(workspace)
        await self.db.set_design_commit_hash(issue.id, design_commit)

        # Post design as issue comment (not PR)
        design_content = design_path.read_text() if design_path.exists() else "Design document created."
        await self.github.post_comment(
            issue.repo_owner, issue.repo_name, issue.issue_number,
            f"## Design for: {issue.title}\n\n{design_content}\n\n---\nPlease review this design and comment with your feedback.\nReply \"approve\" or \"LGTM\" to proceed to implementation.",
        )

        logger.info("Completed designing for issue %d", issue.id)
        if self.audit:
            await self.audit.log("phase_transition", "design_review",
                                  issue_id=issue.id, success=True)
        return PhaseResult(next_phase="design_review")
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_phases/test_designing.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/phases/designing.py tests/test_phases/test_designing.py
git commit -m "feat: add designing phase handler"
```

---

### Task 11: Create Design Review Phase Handler

**Files:**
- Create: `src/remote_agent/phases/design_review.py`
- Create: `tests/test_phases/test_design_review.py`

**Step 1: Write the failing tests**

Create `tests/test_phases/test_design_review.py`:

```python
import pytest
from unittest.mock import AsyncMock
from remote_agent.phases.design_review import DesignReviewHandler
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.agent import CommentInterpretation


@pytest.fixture
def deps():
    return {
        "db": AsyncMock(),
        "github": AsyncMock(),
        "agent_service": AsyncMock(),
    }

@pytest.fixture
def handler(deps):
    return DesignReviewHandler(deps["db"], deps["github"], deps["agent_service"])

@pytest.fixture
def review_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="Need OAuth2", phase="design_review")


async def test_approve_transitions_to_planning(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    result = await handler.handle(review_issue, event)

    assert result.next_phase == "planning"
    deps["db"].set_design_approved.assert_called_once_with(1, True)
    # Confirmation posted on the issue
    deps["github"].post_comment.assert_called_once()


async def test_revise_transitions_to_designing(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Change approach"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="revise")

    result = await handler.handle(review_issue, event)

    assert result.next_phase == "designing"
    deps["db"].create_event.assert_called_once()


async def test_question_posts_answer_and_stays(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Why this approach?"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="question")
    deps["agent_service"].answer_question.return_value = "Because it's simpler."

    result = await handler.handle(review_issue, event)

    assert result.next_phase == "design_review"
    deps["agent_service"].answer_question.assert_called_once()
    deps["github"].post_comment.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phases/test_design_review.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement minimal code**

Create `src/remote_agent/phases/design_review.py`:

```python
from __future__ import annotations
import logging

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService

logger = logging.getLogger(__name__)


class DesignReviewHandler:
    def __init__(self, db: Database, github: GitHubService, agent_service: AgentService,
                 audit=None):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        comment_body = event.payload.get("body", "")

        interpretation = await self.agent_service.interpret_comment(
            comment=comment_body, context="design_review",
            issue_title=issue.title, issue_id=issue.id,
        )
        logger.info("Design review comment interpreted as: %s", interpretation.intent)
        if self.audit:
            await self.audit.log(
                "comment_classification", interpretation.intent,
                issue_id=issue.id, success=True,
            )

        if interpretation.intent == "approve":
            await self.db.set_design_approved(issue.id, True)
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.issue_number,
                "Design approved. Starting planning and implementation...",
            )
            await self.db.create_event(issue.id, "revision_requested", {})
            if self.audit:
                await self.audit.log("phase_transition", "planning",
                                      issue_id=issue.id, success=True)
            return PhaseResult(next_phase="planning")

        elif interpretation.intent == "revise":
            await self.db.create_event(issue.id, "revision_requested", event.payload)
            return PhaseResult(next_phase="designing")

        elif interpretation.intent == "question":
            answer = await self.agent_service.answer_question(
                question=comment_body, context="design_review",
                issue_title=issue.title, issue_body=issue.body or "",
            )
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.issue_number, answer,
            )
            return PhaseResult(next_phase="design_review")

        return PhaseResult(next_phase="design_review")
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_phases/test_design_review.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/phases/design_review.py tests/test_phases/test_design_review.py
git commit -m "feat: add design_review phase handler"
```

---

### Task 12: Rewrite Planning Phase Handler

**Files:**
- Modify: `src/remote_agent/phases/planning.py:1-88`
- Modify: `tests/test_phases/test_planning.py:1-110`

**Step 1: Write the failing tests**

Rewrite `tests/test_phases/test_planning.py`:

```python
async def test_planning_reads_design_and_saves_plan_to_temp(handler, deps, new_issue, new_issue_event):
    # Issue must have design_approved=True and branch already set
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="planning",
                  branch_name="agent/issue-42", design_approved=True)
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="## Design doc content"):
        result = await handler.handle(issue, event)

    assert result.next_phase == "implementing"
    # Verify design_content passed to agent
    call_kwargs = deps["agent_service"].run_planning.call_args.kwargs
    assert call_kwargs["design_content"] == "## Design doc content"
    # Verify plan path stored on issue
    deps["db"].set_plan_path.assert_called_once()
    # Verify auto-transition event created
    deps["db"].create_event.assert_called_once()
    # NO PR creation (PR created by implementation handler)
    deps["github"].create_pr.assert_not_called()
    # NO comment posted (no human gate)
    deps["github"].post_comment.assert_not_called()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phases/test_planning.py -v`
Expected: FAIL

**Step 3: Implement minimal code**

Rewrite `src/remote_agent/phases/planning.py`:

Key changes:
- Remove PR creation (moved to implementation handler)
- Remove "please review" comment (no human gate)
- Read design doc from branch as input
- Agent writes plan to workspace, handler moves to `{config.workspace.base_dir}/.plans/issue-{N}-plan.md`
- Store plan path on issue record via `db.set_plan_path()`
- Auto-transition to implementing via `db.create_event()`
- Remove `existing_plan`/`feedback` handling (plan is internal)
- Handler needs access to config for `workspace.base_dir` — add to constructor or pass through

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_phases/test_planning.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/phases/planning.py tests/test_phases/test_planning.py
git commit -m "refactor: rewrite planning handler — internal phase, plan to temp storage"
```

---

### Task 13: Enhance Implementation Phase Handler

**Files:**
- Modify: `src/remote_agent/phases/implementation.py:1-63`
- Modify: `tests/test_phases/test_implementation.py:1-66`

**Step 1: Write the failing tests**

Rewrite/add tests in `tests/test_phases/test_implementation.py`:

```python
async def test_implementation_reads_design_and_plan(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="implementing",
                  branch_name="agent/issue-42", plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=3.0, input_tokens=500, output_tokens=1000,
    )

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", side_effect=["## Design", "## Plan"]):
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    call_kwargs = deps["agent_service"].run_implementation.call_args.kwargs
    assert call_kwargs["design_content"] == "## Design"
    assert call_kwargs["plan_content"] == "## Plan"
    assert call_kwargs["issue_body"] == "Need OAuth2"


async def test_implementation_creates_pr_when_none_exists(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="implementing",
                  branch_name="agent/issue-42", plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=3.0, input_tokens=500, output_tokens=1000,
    )
    deps["github"].create_pr.return_value = 10

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", side_effect=["## Design", "## Plan"]):
        result = await handler.handle(issue, event)

    deps["github"].create_pr.assert_called_once()
    deps["db"].update_issue_pr.assert_called_once_with(1, 10)


async def test_implementation_skips_pr_creation_when_exists(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="implementing",
                  branch_name="agent/issue-42", pr_number=10,
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=2, issue_id=1, event_type="revision_requested",
                  payload={"body": "Fix the tests"})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="sess-2", cost_usd=2.0, input_tokens=300, output_tokens=600,
    )

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", side_effect=["## Design", "## Plan"]):
        result = await handler.handle(issue, event)

    deps["github"].create_pr.assert_not_called()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phases/test_implementation.py -v`
Expected: FAIL

**Step 3: Implement minimal code**

Enhance `src/remote_agent/phases/implementation.py`:

Key changes:
- Read design doc from workspace branch
- Read plan from `issue.plan_path` (temp storage)
- Pass `design_content`, `issue_body`, and `plan_content` to `agent_service.run_implementation()`
- Create PR if `issue.pr_number` is None (moved from planning handler)
- Mark PR ready if it already exists
- Handle feedback from code_review revision

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_phases/test_implementation.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/phases/implementation.py tests/test_phases/test_implementation.py
git commit -m "feat: enhance implementation handler — read design/plan, create PR"
```

---

### Task 14: Update Code Review Phase Handler

**Files:**
- Modify: `src/remote_agent/phases/code_review.py:1-71`
- Modify: `tests/test_phases/test_code_review.py:1-72`

**Step 1: Write the failing tests**

Rewrite/add tests in `tests/test_phases/test_code_review.py`:

```python
async def test_back_to_design_resets_state(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42",
                  design_commit_hash="abc123",
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="new_comment",
                  payload={"body": "rethink the design"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="back_to_design")

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", side_effect=["## Design", "## Plan"]):
        result = await handler.handle(issue, event)

    assert result.next_phase == "designing"
    deps["db"].set_design_approved.assert_called_once_with(1, False)
    deps["github"].mark_pr_draft.assert_called_once()
    deps["workspace_mgr"].reset_to_commit.assert_called_once_with(
        issue.workspace_path, "abc123", "agent/issue-42")
    deps["db"].clear_plan_path.assert_called_once_with(1)
    # Feedback posted on ISSUE, not PR
    post_calls = deps["github"].post_comment.call_args_list
    issue_comment = [c for c in post_calls if c[0][2] == 42]
    assert len(issue_comment) >= 1


async def test_approve_cleans_plan(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42",
                  plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Ship it!"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", side_effect=["## Design", "## Plan"]):
        result = await handler.handle(issue, event)

    assert result.next_phase == "completed"
    deps["db"].clear_plan_path.assert_called_once_with(1)


async def test_question_answered_with_context(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="code_review",
                  pr_number=10, plan_path="/tmp/.plans/issue-42-plan.md")
    event = Event(id=1, issue_id=1, event_type="new_comment",
                  payload={"body": "Why this approach?"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="question")
    deps["agent_service"].answer_question.return_value = "Per the design doc..."

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", side_effect=["## Design", "## Plan"]):
        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    deps["agent_service"].answer_question.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phases/test_code_review.py -v`
Expected: FAIL

**Step 3: Implement minimal code**

Update `src/remote_agent/phases/code_review.py`:

Key changes:
- Read design doc from workspace and plan from `issue.plan_path` for context
- Pass `design_content` and `plan_content` to `interpret_comment()`
- Rename `back_to_planning` → `back_to_design`
- `back_to_design`: reset to `design_commit_hash`, mark PR draft, mark `design_approved=False`, clean plan via `clear_plan_path()`, post feedback on **issue** (not PR), transition to `designing`
- `approve`: clean plan via `clear_plan_path()`, then existing cleanup
- `question`: call `answer_question()` with full context (design + plan), post answer on PR
- `revise`: existing behavior (transition to implementing with feedback)

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_phases/test_code_review.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/phases/code_review.py tests/test_phases/test_code_review.py
git commit -m "feat: update code_review handler — back_to_design, plan cleanup, AI questions"
```

---

### Task 15: Update Poller for Design Review Phase

**Files:**
- Modify: `src/remote_agent/poller.py:52-89`
- Modify: `tests/test_poller.py`

**Step 1: Write the failing test**

Add to `tests/test_poller.py`:

```python
async def test_polls_issue_comments_for_design_review(config, db, github, sample_issue_data):
    """design_review issues should poll for issue comments, not PR comments."""
    poller = Poller(config, db, github)
    issue_id = await db.create_issue("owner", "repo", sample_issue_data)
    await db.update_issue_phase(issue_id, "design_review")

    github.list_issues.return_value = [
        {"number": 42, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    github.get_pr_comments.return_value = [
        {"id": 100, "body": "Looks good", "author": "testuser", "created_at": "2026-01-01"}
    ]

    await poller.poll_once()

    # Should have polled issue comments (issue number 42, not a PR number)
    github.get_pr_comments.assert_called()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_poller.py::test_polls_issue_comments_for_design_review -v`
Expected: FAIL

**Step 3: Implement minimal code**

Update `src/remote_agent/poller.py` section 3 (line 52-89):

The `design_review` phase needs to poll **issue comments** (using the issue number) rather than PR comments. The issue doesn't have a PR yet at this stage.

Add handling in the review issues loop:
- For issues in `design_review`: use `issue.issue_number` to poll for comments via `get_pr_comments()` (which uses the issues API endpoint that works for both issues and PRs). Track using `last_issue_comment_id` instead of `last_comment_id`.
- For issues in `code_review`/`error`: existing behavior (poll PR comments).

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_poller.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/remote_agent/poller.py tests/test_poller.py
git commit -m "feat: poll issue comments for design_review phase"
```

---

### Task 16: Update Dispatcher — Phase Registry and Routing

**Files:**
- Modify: `src/remote_agent/dispatcher.py:1-168`
- Modify: `tests/test_dispatcher.py:1-173`

**Step 1: Write the failing tests**

Add/update tests in `tests/test_dispatcher.py`:

```python
async def test_routes_new_issue_to_designing(deps, dispatcher):
    """New issues now route to designing, not planning."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="t", body="b", phase="new")
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})

    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue

    await dispatcher.process_events()

    deps["db"].update_issue_phase.assert_called_with(1, "design_review")


async def test_design_review_approve_routes_to_planning(deps, dispatcher):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="t", body="b", phase="design_review")
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})

    target = dispatcher._determine_target_phase(issue, event)
    assert target == "design_review"


async def test_planning_auto_transitions_to_implementing(deps, dispatcher):
    """Planning creates a revision_requested event that routes to implementing."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="t", body="b", phase="planning", design_approved=True)
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})

    target = dispatcher._determine_target_phase(issue, event)
    assert target == "implementing"


async def test_reopen_clears_design_approved(deps, dispatcher):
    """Reopen should clear design_approved, not plan_approved."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="t", body="b", phase="completed", design_approved=True,
                  pr_number=5, branch_name="agent/issue-1")
    event = Event(id=1, issue_id=1, event_type="reopen", payload={})

    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue

    await dispatcher.process_events()

    deps["db"].set_design_approved.assert_called_with(1, False)
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dispatcher.py::test_routes_new_issue_to_designing -v`
Expected: FAIL

**Step 3: Implement minimal code**

Update `src/remote_agent/dispatcher.py`:

1. Update imports:
   - Remove: `from remote_agent.phases.plan_review import PlanReviewHandler`
   - Add: `from remote_agent.phases.designing import DesigningHandler`
   - Add: `from remote_agent.phases.design_review import DesignReviewHandler`

2. Update `__init__`:
   - Remove: `self._plan_review = PlanReviewHandler(...)`
   - Add: `self._designing = DesigningHandler(db, github, agent_service, workspace_mgr, audit=audit)`
   - Add: `self._design_review = DesignReviewHandler(db, github, agent_service, audit=audit)`

3. Update `_get_handler`:
   - `"designing"` → `self._designing`
   - `"design_review"` → `self._design_review`
   - Remove: `"plan_review"` → `self._plan_review`

4. Update `_determine_target_phase`:
   - `new_issue` → `"designing"` (was `"planning"`)
   - `reopen` → `"designing"` (was `"planning"`)
   - `revision_requested` with `designing`/`design_review` → `"designing"`
   - `revision_requested` with `planning` → `"implementing"` (auto-transition)
   - `revision_requested` with `implementing`/`code_review` → `"implementing"` if `design_approved` else `"designing"`
   - `new_comment` with `design_review` → `"design_review"` (was `plan_review`)
   - Remove all `plan_review` references
   - `error` recovery: `"implementing"` if `design_approved` else `"designing"`

5. Update budget check (line 74): add `"designing"` to the budget-checked phases.

6. Update reopen handling (line 99): `set_plan_approved` → `set_design_approved`.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dispatcher.py -v`
Expected: PASS (update existing tests that reference old phase names)

**Step 5: Commit**

```bash
git add src/remote_agent/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: update dispatcher — designing/design_review phases, remove plan_review"
```

---

### Task 17: Delete Plan Review Handler and Tests

**Files:**
- Delete: `src/remote_agent/phases/plan_review.py`
- Delete: `tests/test_phases/test_plan_review.py`

**Step 1: Verify no remaining imports**

Search for remaining references:

Run: `grep -r "plan_review" src/ tests/ --include="*.py" -l`
Expected: No files should reference `plan_review` (after Task 16 removed dispatcher references). If any remain, they are bugs from previous tasks.

**Step 2: Delete the files**

```bash
rm src/remote_agent/phases/plan_review.py
rm tests/test_phases/test_plan_review.py
```

**Step 3: Run full test suite**

Run: `pytest -v`
Expected: PASS — no test references the deleted files

**Step 4: Commit**

```bash
git add -A
git commit -m "chore: delete plan_review phase handler (replaced by design_review)"
```

---

### Task 18: Rewrite Integration Test for New Lifecycle

**Files:**
- Modify: `tests/test_integration.py:1-288`

**Step 1: Write the failing test**

Rewrite `test_full_lifecycle_happy_path` for the new flow:

```python
async def test_full_lifecycle_happy_path(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    """Test: new issue -> designing -> design_review -> approve -> planning -> implementing -> code_review -> approve -> completed"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
    # Override dispatcher handlers to use our mocks
    for handler_name in ("_designing", "_design_review", "_planning", "_implementation", "_code_review"):
        handler = getattr(dispatcher, handler_name)
        if hasattr(handler, "agent_service"):
            handler.agent_service = agent_service
        if hasattr(handler, "workspace_mgr"):
            handler.workspace_mgr = workspace_mgr
        if hasattr(handler, "github"):
            handler.github = github

    # Step 1: Poller detects new issue
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "new"

    # Step 2: Dispatcher routes to designing
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "abc123"
    agent_service.run_designing.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )

    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "design_review"
    assert issue.design_commit_hash == "abc123"

    # Step 3: Human approves design (comment on issue)
    github.get_pr_comments.return_value = [
        {"id": 100, "body": "LGTM", "author": "testuser", "created_at": "2026-01-01"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "planning"
    assert issue.design_approved is True

    # Step 4: Planning runs automatically (triggered by event from design_review)
    agent_service.run_planning.return_value = AgentResult(
        success=True, session_id="s2", cost_usd=1.5, input_tokens=200, output_tokens=400,
    )

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="## Design content"):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "implementing"
    assert issue.plan_path is not None

    # Step 5: Implementation runs automatically (triggered by event from planning)
    agent_service.run_implementation.return_value = AgentResult(
        success=True, session_id="s3", cost_usd=5.0, input_tokens=1000, output_tokens=2000,
    )
    github.create_pr.return_value = 5

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", side_effect=["## Design", "## Plan"]):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "code_review"
    assert issue.pr_number == 5

    # Step 6: Human approves code (comment on PR)
    github.get_pr_comments.return_value = [
        {"id": 200, "body": "Ship it!", "author": "testuser", "created_at": "2026-01-02"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "completed"
    assert issue.plan_path is None  # Cleaned up

    # Verify audit trail
    audit_lines = audit_file.read_text().strip().split("\n")
    audit_records = [json.loads(line) for line in audit_lines]
    categories_and_actions = [(r["category"], r["action"]) for r in audit_records]

    assert ("phase_transition", "design_review") in categories_and_actions
    assert ("phase_transition", "planning") in categories_and_actions
    assert ("phase_transition", "code_review") in categories_and_actions
    assert ("phase_transition", "completed") in categories_and_actions
```

Also rewrite `test_review_comment_triggers_revision` and `test_completed_issue_reopen_lifecycle` for the new phase names and flow.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_integration.py -v`
Expected: FAIL — old lifecycle assertions don't match new flow

**Step 3: Implement — tests are the implementation**

The integration test IS the implementation for this task. No production code changes — just the test rewrites to validate the full new lifecycle works end-to-end with the changes from Tasks 1-17.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_integration.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: rewrite integration tests for new designing/design_review lifecycle"
```

---

## Testing Strategy

After all tasks, run the full test suite:

```bash
pytest -v
```

All tests should pass. Key verification points:
- New lifecycle: `new → designing → design_review → planning → implementing → code_review → completed`
- Design review on issue comments (not PR)
- Plan stored in temp location, cleaned up on completion
- back_to_design resets to design commit hash
- No references to `plan_review` anywhere in codebase
- Integration test covers happy path end-to-end

## Risks and Considerations

1. **DB migration for existing issues**: Issues currently in `plan_review` phase will be migrated to `designing`. Issues in other phases may need manual attention.
2. **Poller complexity**: Polling issue comments for `design_review` vs PR comments for `code_review` adds a branching path in the poller.
3. **Plan temp storage**: The `.plans/` directory needs to be created on first use. Handler should `mkdir -p` before writing.
4. **AI-powered comment classification**: Switching from regex to AI for `interpret_comment` adds cost and latency. Keeping regex as fast-path fallback for obvious cases (LGTM, APPROVED review state) is recommended.
5. **Large agent run for implementing**: The implementing orchestrator now handles planning+task execution+reviews in one session-like flow (across the internal planning→implementing transition). Budget management is important.
