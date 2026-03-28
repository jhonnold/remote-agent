# Prompt Flow Alignment Design

## Summary

Restructure the remote agent's prompt flow to match the superpowers brainstorming/planning/implementation skill chain. Every human interaction point in the superpowers flow gets either a GitHub human gate or a sub-agent replacement. No interaction points are simply removed.

## Decisions

- **Two human gates:** After design (GitHub issue comment) and after all tasks complete (GitHub PR comment). All other stages are internal.
- **Sub-agent replacement:** Wherever the superpowers flow expects a human response (clarifying questions, approach selection, design approval, plan review, implementer questions), a sub-agent responds instead.
- **Issue advocate:** The sub-agent answering questions is codebase-informed but issue-first. It answers from the issue text, supplements with codebase evidence, and flags when inferring beyond what the issue states.
- **Context isolation:** Each phase starts fresh. The only information crossing phase boundaries is explicitly passed: issue body, design doc content, plan content.
- **Design doc committed:** The design doc lives in `docs/plans/issue-{N}-design.md` on the branch as a permanent architectural record.
- **Plan transient:** The implementation plan is stored at `{workspace.base_dir}/.plans/issue-{N}-plan.md`, outside the git tree, never committed. Cleaned up on completion or back_to_design.
- **No haiku models:** All sub-agents use sonnet. Orchestrators for creative/architectural work (designing, planning) use opus.

## State Machine

```
new -> designing -> design_review -> planning -> implementing -> code_review -> completed
          ^              |                                           |
          +-- revise ----+                                           |
          ^                                                          |
          +-------------- back_to_design ----------------------------+
                                                                     |
                          implementing <---- revise -----------------+
```

### Phase Responsibilities

| Phase | Agent Run? | Human Gate? | Input | Output |
|---|---|---|---|---|
| `new` | No | No | GitHub issue detected | Event created |
| `designing` | Yes — design orchestrator | No | Issue body | Design doc (committed, posted as issue comment) |
| `design_review` | No | **Yes** — issue comment | Human comment + issue + design doc | Classify: approve / revise / question |
| `planning` | Yes — planning orchestrator | No | Issue + design doc | Plan file (temp storage) |
| `implementing` | Yes — impl orchestrator | No | Issue + design doc + plan | Code on branch, PR created/updated |
| `code_review` | No | **Yes** — PR comment | Human comment + issue + design doc + plan | Classify: approve / revise / question / back_to_design |
| `completed` | No | No | — | Workspace + temp plan cleanup |

### Revision Flows

**design_review gate:**
- **approve** -> planning (design locked)
- **revise** -> designing (with feedback, previous design preserved where not criticized)
- **question** -> agent answers on issue, stays in design_review

**code_review gate:**
- **approve** -> completed (workspace cleanup, PR left for human to merge)
- **revise** -> implementing (with feedback, affected tasks only)
- **question** -> agent answers on PR with full context, stays in code_review
- **back_to_design** -> reset branch to design commit, mark PR draft, clean temp plan, post feedback on issue, transition to designing

## Designing Phase

The design orchestrator mirrors the superpowers brainstorming skill's collaborative dialogue, with sub-agents replacing the human at each interaction point.

### Orchestrator

Role: Expert software architect who brainstorms designs through structured dialogue. Must follow the process — no skipping Q&A or approach proposals, even for "simple" issues.

Model: `planning_model` (opus)

### Sub-Agents

| Sub-Agent | Role | Model | Tools |
|---|---|---|---|
| `codebase-explorer` | Maps project structure, conventions, relevant code, testing patterns | sonnet | Read, Glob, Grep |
| `issue-advocate` | Answers clarifying questions as proxy for issue author. Codebase-informed, answers from issue first, supplements with codebase evidence, flags inferences. | sonnet | Read, Glob, Grep |
| `design-critic` | Reviews proposed design sections for completeness, feasibility, alignment with issue goals, YAGNI violations. Can reject sections. | sonnet | Read, Glob, Grep |

### Process

1. **Explore context** — Dispatch `codebase-explorer` to map relevant codebase
2. **Clarifying questions** — Ask `issue-advocate` one at a time. Focus on purpose, constraints, success criteria. Continue until sufficient understanding.
3. **Propose 2-3 approaches** — Present to `issue-advocate` with trade-offs and recommendation. Advocate evaluates which best serves the issue's goals.
4. **Present design sections** — Architecture, components, data flow, error handling, testing. Present one at a time to `design-critic`. Revise sections the critic rejects before proceeding.
5. **Write design doc** — Save to `docs/plans/issue-{N}-design.md`

### Output

Design doc file on the branch. Phase handler commits, pushes, and posts content as an issue comment for human review.

On revision (from design_review), the orchestrator receives previous design + feedback and focuses sub-agent dialogue on criticized sections.

## Planning Phase

Runs automatically after design approval. No human gate.

### Orchestrator

Role: Expert software architect creating a detailed implementation plan from an approved design. Follows superpowers `writing-plans` task granularity — each step is one action (2-5 minutes).

Model: `planning_model` (opus)

### Sub-Agents

| Sub-Agent | Role | Model | Tools |
|---|---|---|---|
| `codebase-explorer` | Maps exact file paths, line ranges, existing patterns, test conventions relevant to the plan | sonnet | Read, Glob, Grep |
| `plan-reviewer` | Reviews plan against design doc. Checks: every design requirement has a task, correct task ordering, valid file paths, correct test commands, no missing/extra work (YAGNI). Can reject. | sonnet | Read, Glob, Grep |

### Process

1. **Read design doc** — Understand what was approved
2. **Explore codebase** — Dispatch `codebase-explorer` for exact file paths, line ranges, patterns
3. **Write plan** — Bite-sized tasks following superpowers granularity:
   - Each task independently implementable
   - Each step is a single action: write test -> run (verify fail) -> implement -> run (verify pass) -> commit
   - Exact file paths with line ranges for modifications
   - Complete code in the plan (not "add validation")
   - Exact test commands with expected output
4. **Internal plan review** — Dispatch `plan-reviewer` with plan + design doc. Revise if issues found (max 3 iterations).
5. **Save plan** — Write to `{workspace.base_dir}/.plans/issue-{N}-plan.md`

### Plan Format

```markdown
# [Feature Name] Implementation Plan

**Issue:** #<number>
**Design:** docs/plans/issue-{N}-design.md
**Goal:** [One sentence]
**Architecture:** [2-3 sentences]

---

### Task 1: [Component Name]

**Files:**
- Create: `exact/path/file.py`
- Modify: `exact/path/existing.py:123-145`
- Test: `tests/exact/path/test_file.py`

**Step 1: Write failing test**
[exact code]

**Step 2: Run test, verify failure**
Run: `pytest tests/path/test.py::test_name -v`
Expected: FAIL with "[specific error]"

**Step 3: Implement minimal code**
[exact code]

**Step 4: Run tests, verify pass**
Run: `pytest tests/path/test.py::test_name -v`
Expected: PASS

**Step 5: Commit**
`git add [files] && git commit -m "feat: [description]"`
```

### Transition

Automatic transition to `implementing`. Phase handler stores plan path on issue record and creates event to drive implementation handler.

## Implementing Phase

Mirrors superpowers `subagent-driven-development` — fresh sub-agent per task, two-stage review (spec then quality), issue-advocate for questions, final holistic review.

### Orchestrator

Role: Senior developer dispatching sub-agents per task. Does not write code itself. Provides full task text + scene-setting context to each sub-agent.

Model: `orchestrator_model` (sonnet)

### Sub-Agents

| Sub-Agent | Role | Model | Tools |
|---|---|---|---|
| `implementer` | Implements a single task. Follows TDD. Asks questions before/during work. Self-reviews (completeness, quality, discipline, testing) before reporting. Fixes self-review issues before reporting. | sonnet | Read, Write, Edit, Bash, Glob, Grep |
| `issue-advocate` | Answers implementer questions. Same codebase-informed, issue-first role as in designing. | sonnet | Read, Glob, Grep |
| `spec-reviewer` | Adversarial spec compliance verification. Does NOT trust implementer's report. Reads actual code. Checks: missing requirements, extra/unneeded work, misunderstandings. Structured DO/DON'T lists. | sonnet | Read, Glob, Grep |
| `code-reviewer` | Code quality review after spec passes. Receives git SHA range. Reports: Strengths, Issues (Critical/Important/Minor with file:line), Assessment. | sonnet | Read, Glob, Grep |
| `final-reviewer` | Holistic review of entire implementation. Reviews cross-task integration, architecture coherence, full test suite results. | sonnet | Read, Glob, Grep, Bash |

### Per-Task Process

```
For each task in plan (sequential, never parallel):

  1. Extract full task text + scene-setting context
     (where this fits, what was completed before, dependencies)

  2. Dispatch implementer with full task text + context
     - Implementer asks questions?
       YES -> Dispatch issue-advocate to answer -> re-dispatch implementer with answers
       NO  -> Implementer proceeds
     - Implementer implements, tests, commits, self-reviews
     - Implementer reports back

  3. Dispatch spec-reviewer with task spec + implementer's report
     - Approved? -> proceed to step 4
     - Issues?   -> Implementer fixes -> spec-reviewer re-reviews (max 3 loops)

  4. Dispatch code-reviewer with task summary + git SHA range
     - Approved? -> mark task complete, next task
     - Issues?   -> Implementer fixes -> code-reviewer re-reviews (max 3 loops)
```

### After All Tasks

1. Run full test suite (verification-before-completion)
2. Dispatch `final-reviewer` with: design doc, plan summary, full git diff from branch base, test suite output
3. If final-reviewer finds issues -> implementer fixes -> re-review

### Orchestrator Rules

- Execute tasks in order, never parallelize implementer sub-agents
- Spec review BEFORE code quality review (never reversed)
- Never skip reviews (either one)
- Never proceed with unfixed issues
- Provide full task text to implementer (never make it read the plan file)
- Include scene-setting context with every implementer dispatch
- Answer implementer questions via issue-advocate before proceeding
- If blocked after 3 review iterations on either review, stop and report

### Output

Phase handler commits implementation to branch, creates PR (or pushes to existing), posts summary with test results. Transitions to `code_review`.

## Prompt File Structure

```
src/remote_agent/prompts/
  designing.py        # Design orchestrator system/user prompts
  planning.py         # Planning orchestrator system/user prompts (rewritten)
  implementation.py   # Implementation orchestrator system/user prompts (enhanced)
  review.py           # AI-powered comment classification (activated)
  subagents.py        # All sub-agent prompt definitions, centralized
```

### subagents.py

Each sub-agent is a function returning its prompt text:

```python
# Shared across phases
def codebase_explorer_prompt() -> str: ...
def issue_advocate_prompt(issue_body: str) -> str: ...  # parameterized

# Designing phase
def design_critic_prompt() -> str: ...

# Planning phase
def plan_reviewer_prompt() -> str: ...

# Implementation phase
def implementer_prompt() -> str: ...
def spec_reviewer_prompt() -> str: ...
def code_quality_reviewer_prompt() -> str: ...
def final_reviewer_prompt() -> str: ...
```

The `issue_advocate_prompt` is parameterized — the issue body is baked into the system prompt so the advocate can act as the issue author's proxy.

### agent.py Changes

```python
class AgentService:
    async def run_designing(self, *, issue_number, issue_title, issue_body,
                            cwd, issue_id, existing_design=None,
                            feedback=None) -> AgentResult: ...

    async def run_planning(self, *, issue_number, issue_title, issue_body,
                           design_content, cwd, issue_id) -> AgentResult: ...

    async def run_implementation(self, *, plan_content, design_content,
                                 issue_title, issue_body, cwd, issue_id,
                                 feedback=None) -> AgentResult: ...

    async def interpret_comment(self, *, comment, context, issue_title,
                                issue_id, design_content=None,
                                plan_content=None) -> CommentInterpretation: ...

    async def answer_question(self, *, question, context, issue_title,
                              issue_body, design_content=None,
                              plan_content=None) -> str: ...
```

### Model Assignments

| Agent | Config Field | Default |
|---|---|---|
| Design orchestrator | `planning_model` | opus |
| Planning orchestrator | `planning_model` | opus |
| Implementation orchestrator | `orchestrator_model` | sonnet |
| All sub-agents | — | sonnet |
| Comment classifier | `review_model` | sonnet |
| Question answerer | `review_model` | sonnet |

## Phase Handler Changes

### New Files

**`phases/designing.py`:**
1. Ensure workspace + branch (creates branch on first run)
2. Read existing design doc if revision
3. Extract feedback from event if revision
4. Call `agent_service.run_designing()`
5. Commit design doc to branch, push
6. Store design commit hash on issue record
7. Post design doc content as issue comment (new comment on revision, preserving history)
8. Transition to `design_review`

**`phases/design_review.py`:**
1. Extract comment body from event
2. Call `agent_service.interpret_comment(context="design_review")`
3. approve -> mark design approved, post confirmation on issue, transition to `planning`
4. revise -> create revision event with feedback, transition to `designing`
5. question -> call `agent_service.answer_question()`, post answer on issue, stay in `design_review`

### Rewritten Files

**`phases/planning.py`:**
1. Ensure workspace + branch
2. Read design doc from branch
3. Call `agent_service.run_planning()` (includes internal sub-agent plan review)
4. Move plan from workspace to `{workspace.base_dir}/.plans/issue-{N}-plan.md`
5. Store plan path on issue record
6. Transition automatically to `implementing` (create event)

### Enhanced Files

**`phases/implementation.py`:**
1. Ensure workspace + branch
2. Read design doc from branch
3. Read plan from temp path (from issue record)
4. Extract feedback from event if revision from code_review
5. Call `agent_service.run_implementation()` (per-task reviews, final review, test suite)
6. Commit and push implementation to branch
7. If no PR: create PR with summary + test results. If PR exists: post update comment.
8. Transition to `code_review`

**`phases/code_review.py`:**
1. Extract comment body from event
2. Read design doc from branch, plan from temp path
3. Call `agent_service.interpret_comment(context="code_review", design_content, plan_content)`
4. approve -> post confirmation on PR, clean workspace + temp plan, transition to `completed`
5. revise -> create revision event with feedback, transition to `implementing`
6. back_to_design -> reset branch to design commit hash, mark PR draft, mark design unapproved, clean temp plan, post feedback on issue, create revision event, transition to `designing`
7. question -> call `agent_service.answer_question()` with full context, post on PR, stay in `code_review`

### Deleted Files

**`phases/plan_review.py`** — Human plan review gate removed.

### Dispatcher Updates

- Add `designing`, `design_review` to phase handler registry
- Remove `plan_review` from registry

## Database & Model Changes

### Field Changes on Issue Model

| Old Field | New Field | Reason |
|---|---|---|
| `plan_approved` | `design_approved` | Gate moved from plan to design |
| `plan_commit_hash` | `design_commit_hash` | Tracks design commit for back_to_design |
| — | `plan_path` (`str \| None`) | Absolute path to temp plan file |

### DB Method Changes

| Old | New |
|---|---|
| `set_plan_approved()` | `set_design_approved()` |
| `set_plan_commit_hash()` | `set_design_commit_hash()` |
| — | `set_plan_path()` |
| — | `clear_plan_path()` |

### Phase Values

Valid: `"new"`, `"designing"`, `"design_review"`, `"planning"`, `"implementing"`, `"code_review"`, `"completed"`, `"error"`

Removed: `"plan_review"`

Migration: existing issues in `plan_review` transition to `designing`.

### Config Change

`orchestrator_model` default changes from `"haiku"` to `"sonnet"`.

## Human Interaction Point Mapping

| Superpowers Human Interaction | Remote Agent Replacement |
|---|---|
| Brainstorming: clarifying questions answered | `issue-advocate` sub-agent |
| Brainstorming: approach selection | `issue-advocate` sub-agent evaluates |
| Brainstorming: design section approval | `design-critic` sub-agent reviews |
| Design approval gate | **Human gate** (GitHub issue comment) |
| Plan review / execution choice | `plan-reviewer` sub-agent |
| Implementer asks questions mid-task | `issue-advocate` sub-agent answers |
| Between-batch feedback | Orchestrator reviews results internally |
| Code review gate | **Human gate** (GitHub PR comment) |
| Finishing branch choice | Predetermined — PR created for human to merge |

## File Change Summary

**New:**
- `src/remote_agent/phases/designing.py`
- `src/remote_agent/phases/design_review.py`
- `src/remote_agent/prompts/designing.py`
- `src/remote_agent/prompts/subagents.py`

**Rewritten:**
- `src/remote_agent/prompts/planning.py`
- `src/remote_agent/phases/planning.py`

**Enhanced:**
- `src/remote_agent/prompts/implementation.py`
- `src/remote_agent/phases/implementation.py`
- `src/remote_agent/prompts/review.py`
- `src/remote_agent/phases/code_review.py`
- `src/remote_agent/agent.py`
- `src/remote_agent/models.py`
- `src/remote_agent/db.py`
- `src/remote_agent/config.py`

**Deleted:**
- `src/remote_agent/phases/plan_review.py`

**Tests impacted:**
- All phase handler tests need updating for new signatures and flows
- New tests for designing, design_review handlers
- Integration test rewrite for new lifecycle
- Comment classification tests rewrite (regex -> AI)
