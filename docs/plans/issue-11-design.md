# LLM-as-Judge Prompt Eval Suite Design

**Issue:** #11
**Goal:** Build a separate test suite that uses LLM-as-judge to validate prompt changes don't regress agent output quality.

## Architecture

The eval suite introduces end-to-end prompt regression testing by running agent phases against fixture issues and using an LLM judge to evaluate output quality. It lives in `tests/evals/` as a separate directory from unit tests, gated behind a `@pytest.mark.eval` marker so it never runs during normal `pytest` invocations.

**Execution model:** Each eval test follows a three-step pattern:

1. **Generate** — Call an `AgentService` method (e.g., `run_designing()`) with a fixture issue against a git worktree of the actual project repo, capturing the file artifact written to disk (e.g., `docs/plans/issue-{N}-design.md`). The artifact is the file on disk, NOT `AgentResult.result_text` (which contains the commit message XML, not the design document).
2. **Judge** — Send the captured artifact to a judge LLM (Haiku) with a phase-specific rubric. The judge returns a JSON array of structured scores (1–5 scale) for criteria derived directly from the phase's prompt constraints.
3. **Assert** — Compare scores against minimum thresholds. A test fails if any criterion drops below its threshold.

**Separation from unit tests:**

- A dedicated pytest marker (`eval`) registered in `pyproject.toml` under `[tool.pytest.ini_options]`: `markers = ["eval: LLM-as-judge eval tests (slow, costs money)"]`
- A custom `--run-evals` CLI flag registered via `pytest_addoption` in `tests/evals/conftest.py`, with an autouse fixture that skips all eval tests unless the flag is passed
- Run command: `pytest --run-evals` (explicit opt-in)
- Regular unit tests unaffected: plain `pytest` runs only unit tests (evals show as `SKIPPED`)
- No API key or environment variable configuration is required — the `claude-agent-sdk` automatically authenticates via the local claude-code CLI

**Phase coverage:** Start with the designing phase — it has the most well-defined output structure (a design document with 5 required sections) and the most constrained prompt. Planning and implementation phases can be added as separate follow-up issues once the eval infrastructure is proven. The review/comment-classification phase is excluded since `interpret_comment()` at `src/remote_agent/agent.py:122` is pure regex and doesn't use prompts at runtime.

**Cost control:** Eval tests use a dedicated `AgentConfig` with cost-conscious overrides:

| Parameter | Production | Eval |
|---|---|---|
| `planning_model` | `"opus"` | `"sonnet"` |
| `max_turns` | `200` | `50` |
| `max_budget_usd` | `10.0` | `2.0` |

Cost per full eval suite run: up to `max_budget_usd` ($2.00) for agent generation + negligible Haiku judge cost (~$0.01). The budget cap is enforced by `ClaudeAgentOptions.max_budget_usd` passed through `AgentService._run_query()`.

## Components

### 1. Eval Fixtures (`tests/evals/fixtures/`)

**Purpose:** Store static test data and rubric definitions.

**Files:**

- `sample_issues.py` — Module-level constants for sample issue data:
  ```python
  CACHING_ISSUE = {
      "issue_number": 99,
      "issue_title": "Add caching layer",
      "issue_body": "We need a caching layer for API responses to reduce latency...",
  }
  ```
  One sample issue to start. Promote to parameterized fixtures only when multiple eval scenarios are needed.

- `rubrics.py` — Rubric definitions as dataclasses:
  ```python
  @dataclass
  class Criterion:
      name: str
      description: str
      min_score: int  # 1-5, minimum passing score

  DESIGNING_RUBRIC: list[Criterion] = [...]
  ```

**Designing phase rubric criteria** (derived directly from the constraints in `src/remote_agent/prompts/designing.py`):

| Criterion | Description | Min Score |
|---|---|---|
| Section completeness | Contains all 5 required sections: Architecture, Components, Data Flow, Error Handling, Testing Strategy | 5 |
| Format adherence | Follows the markdown template: has `# [Name] Design` header, `**Issue:**` and `**Goal:**` fields, `##` section headers | 4 |
| Specificity | References concrete file paths, function names, and patterns from the codebase (per MUST constraint in prompt) | 3 |
| No implementation code | Does not contain actual implementation code — only design description (per MUST NOT constraint in prompt) | 4 |
| Coherence | Sections are internally consistent; the architecture logically supports the components described | 3 |

### 2. Eval Configuration (`tests/evals/conftest.py`)

**Purpose:** Provide pytest fixtures for eval tests, handle skip logic, and configure the agent for eval runs.

**CLI flag and skip fixture:**
```python
def pytest_addoption(parser):
    parser.addoption(
        "--run-evals", action="store_true", default=False,
        help="Run LLM-as-judge eval tests (slow, costs money)",
    )

@pytest.fixture(autouse=True)
def _skip_unless_evals(request):
    if not request.config.getoption("--run-evals"):
        pytest.skip("Pass --run-evals to run eval tests")
```

No API key or environment variable is checked. The `claude-agent-sdk` authenticates automatically via the local claude-code CLI session.

**Key fixtures:**

- `eval_config() -> Config` — Constructs a full `Config` with all required fields (following the pattern in `tests/test_integration.py:14-24`) and eval-specific `AgentConfig` overrides:
  ```python
  @pytest.fixture
  def eval_config():
      return Config(
          repos=[RepoConfig(owner="eval", name="eval")],
          users=["eval-user"],
          polling=PollingConfig(interval_seconds=60),
          trigger=TriggerConfig(label="agent"),
          workspace=WorkspaceConfig(base_dir="/tmp/eval"),
          database=DatabaseConfig(path=""),
          agent=AgentConfig(
              planning_model="sonnet",
              max_turns=50,
              max_budget_usd=2.0,
          ),
          logging=LoggingConfig(),
      )
  ```

- `eval_db(tmp_path) -> Database` — Initializes a real SQLite database and seeds a test issue row:
  ```python
  @pytest.fixture
  async def eval_db(tmp_path):
      db = await Database.initialize(str(tmp_path / "eval.db"))
      # Seed issue row; id=1 is guaranteed by fresh auto-increment
      await db.create_issue("eval", "eval", {
          "number": 99,
          "title": "Add caching layer",
          "body": "We need a caching layer...",
      })
      yield db
      await db.close()
  ```

- `eval_agent_service(eval_config, eval_db) -> AgentService` — Constructs `AgentService(eval_config, eval_db)`.

- `eval_workspace(tmp_path) -> str` — Creates a local git worktree of the current project repo for the agent to explore. Uses `git worktree add` (no network clone) to provide a realistic codebase with the full `src/` tree, `pyproject.toml`, and existing phases. Cleanup via `git worktree remove` in fixture teardown:
  ```python
  @pytest.fixture
  async def eval_workspace(tmp_path):
      worktree_path = str(tmp_path / "eval-worktree")
      repo_root = _find_repo_root()  # walks up from __file__ to find .git
      branch = f"eval-{uuid4().hex[:8]}"
      subprocess.run(
          ["git", "worktree", "add", "-b", branch, worktree_path, "HEAD"],
          cwd=repo_root, check=True,
      )
      yield worktree_path
      subprocess.run(["git", "worktree", "remove", "--force", worktree_path],
                      cwd=repo_root, check=True)
      subprocess.run(["git", "branch", "-D", branch],
                      cwd=repo_root, check=True)
  ```
  This gives the agent a full codebase to explore, satisfying the "Specificity" rubric criterion.

### 3. Judge Module (`tests/evals/judge.py`)

**Purpose:** Send an artifact + rubric to a judge LLM and return structured scores.

**Public interface:**
```python
@dataclass
class JudgeScore:
    criterion: str
    score: int  # 1-5
    reasoning: str

@dataclass
class JudgeResult:
    scores: list[JudgeScore]
    passed: bool  # True if all scores >= their min thresholds

class JudgeParseError(Exception):
    """Raised when the judge LLM returns non-JSON or malformed JSON."""
    def __init__(self, raw_response: str):
        super().__init__(
            f"Judge returned invalid JSON. Raw response:\n{raw_response}"
        )

async def judge_output(
    artifact: str,
    rubric: list[Criterion],
    context: str = "",
) -> JudgeResult:
    """Send artifact to judge LLM, return structured scores."""
```

**Implementation details:**

- Uses `claude-agent-sdk`'s `query()` with a judge-specific system prompt instructing the LLM to return a JSON array of `{"criterion": str, "score": int, "reasoning": str}` objects.
- Model: `haiku` for cost efficiency. `max_turns: 1` (single-shot, no tool use).
- The judge system prompt includes the full rubric (criterion names and descriptions) so the judge knows exactly what to evaluate.
- On non-JSON response: raises `JudgeParseError` with the raw response. No retry — parse failures indicate a judge prompt issue, not transient flakiness.

**Dependencies:** `claude-agent-sdk` (already a project dependency).

### 4. Eval Tests (`tests/evals/test_designing_eval.py`)

**Purpose:** The actual eval test cases for the designing phase.

**Tests:**

- `test_designing_produces_quality_design` — Primary eval test:
  ```python
  @pytest.mark.eval
  async def test_designing_produces_quality_design(
      eval_agent_service, eval_db, eval_workspace,
  ):
      try:
          result = await eval_agent_service.run_designing(
              issue_number=99, issue_title="Add caching layer",
              issue_body="We need a caching layer...",
              cwd=eval_workspace, issue_id=1,
          )
      except AgentError as e:
          pytest.fail(f"Agent execution failed: {e}")

      design_path = Path(eval_workspace) / "docs" / "plans" / "issue-99-design.md"
      if not design_path.exists():
          pytest.fail(f"Agent did not produce design file at {design_path}")

      artifact = design_path.read_text()
      judge_result = await judge_output(
          artifact, DESIGNING_RUBRIC,
          context=f"Design for: Add caching layer",
      )
      assert judge_result.passed, _format_failures(judge_result)
  ```

- `test_designing_revision_produces_quality_design` — Revision path eval. Uses a **fixture-provided stub design document** (hardcoded in `sample_issues.py`, NOT output from the prior test) and fixture-provided feedback string. Runs in a **fresh worktree** independent of the first test. Verifies the revised design addresses the feedback while retaining non-criticized sections.

## Data Flow

### Primary flow: Designing eval test

```
1. Developer runs `pytest --run-evals`
2. pytest discovers tests/evals/test_designing_eval.py
3. conftest.py autouse fixture checks --run-evals flag → skip if absent
4. Fixtures initialize:
   a. eval_config → Config with AgentConfig(planning_model="sonnet", max_turns=50, max_budget_usd=2.0)
   b. eval_db → Database.initialize(tmp_path/"eval.db") → seed issue row via create_issue()
   c. eval_agent_service → AgentService(eval_config, eval_db)
   d. eval_workspace → `git worktree add` of project repo → full codebase copy
5. Test calls eval_agent_service.run_designing(cwd=eval_workspace, issue_id=1, ...)
6. AgentService builds prompts via build_designing_system_prompt() / build_designing_user_prompt()
7. AgentService calls claude-agent-sdk query() (SDK authenticates via local claude-code CLI) → SDK calls create_agent_run on eval_db
8. Agent explores eval_workspace (real codebase), writes docs/plans/issue-99-design.md
9. Test reads design file from Path(eval_workspace) / "docs/plans/issue-99-design.md"
10. Test calls judge_output(artifact=design_content, rubric=DESIGNING_RUBRIC)
11. judge.py sends artifact + rubric to Haiku via claude-agent-sdk query(max_turns=1)
12. Haiku returns JSON array of {criterion, score, reasoning}
13. judge.py parses JSON → JudgeResult(scores=[...], passed=True/False)
14. Test asserts judge_result.passed; on failure, prints per-criterion diagnostics
15. Fixture teardown: `git worktree remove`, database close
```

### Failure branch: Agent execution error

```
5b. run_designing() raises AgentError (budget exceeded, SDK timeout)
6b. Test catches AgentError → pytest.fail("Agent execution failed: {error}")
7b. Test reports FAILED with clear diagnostic; judge is never invoked
```

### Failure branch: Judge parse error

```
12b. Haiku returns prose instead of JSON
13b. judge.py raises JudgeParseError(raw_response)
14b. Test reports ERROR (not FAILED) — signals infrastructure issue, not quality regression
```

### Inputs and triggers

- **Trigger:** Developer runs `pytest --run-evals`
- **Inputs:** Static fixture data (sample issue constants), eval config overrides, git worktree of project repo

### Output and side effects

- **Pass/fail:** pytest exit code
- **Diagnostics:** On quality failure, each failing `JudgeScore.reasoning` is printed. On agent failure, the `AgentError` message is shown. On judge parse failure, the raw LLM response is shown.
- **Cost:** Up to `max_budget_usd` ($2.00) for agent generation + ~$0.01 Haiku judge cost per test
- **Side effects:** Temporary git worktree and SQLite database in `tmp_path` (cleaned up by pytest fixtures)

## Error Handling

Four failure modes, each handled distinctly:

### 1. Agent execution failure (`AgentError`)

When `run_designing()` raises `AgentError` (budget exceeded, SDK error, timeout), the test catches it and calls `pytest.fail()` with the original error message. This produces a clear `FAILED` result with the cause visible in output, distinct from a quality failure.

`AgentService._run_query()` at `src/remote_agent/agent.py:158` calls `self.db.create_agent_run()` and `self.db.complete_agent_run()` unconditionally. The eval fixtures provide a real SQLite `Database` (via `eval_db`) so these calls succeed without mocking.

### 2. Missing artifact

If `run_designing()` succeeds but `docs/plans/issue-99-design.md` does not exist at `Path(eval_workspace) / "docs" / "plans" / "issue-99-design.md"`, the test calls `pytest.fail("Agent did not produce design file at {path}")`. The `eval_workspace` path is provided by the fixture.

### 3. Judge parse failure

If the judge LLM returns non-JSON or malformed JSON, `judge_output()` raises `JudgeParseError` with the raw response included. This propagates as an `ERROR` in pytest (not `FAILED`), signaling that the eval infrastructure broke — not that the agent's output quality regressed. An `ERROR` here should be treated as a blocking issue requiring a fix to the judge prompt, not a flaky test to retry.

### 4. Quality failure

When the judge returns valid scores but one or more criteria fall below their minimum threshold, the test fails via `assert judge_result.passed`. The assertion message includes each failing criterion's name, actual score, minimum required score, and the judge's reasoning.

**No retry logic.** Eval tests are inherently non-deterministic due to LLM variance. Retrying masks flakiness rather than surfacing it. If a test fails, the developer inspects the diagnostic output and either fixes the prompt or adjusts the rubric threshold if the failure is spurious.

## Testing Strategy

The eval suite is test infrastructure. It needs its own verification:

### Unit tests for the judge module (`tests/test_judge.py`)

Test `judge.py` in isolation by mocking `claude-agent-sdk`'s `query()` call. These run with the regular `pytest` suite (fast, no LLM calls):

1. **Happy path** — Mock judge returning valid JSON with all scores above thresholds → `JudgeResult.passed == True`
2. **Failure path** — Mock judge returning valid JSON with one score below threshold → `JudgeResult.passed == False`, correct `scores` populated
3. **Parse error** — Mock judge returning prose instead of JSON → raises `JudgeParseError` with raw response
4. **Rubric passthrough** — Verify the judge system prompt includes all criterion names and descriptions from the provided rubric

### Unit tests for fixtures and rubrics (`tests/test_eval_fixtures.py`)

1. **Rubric completeness** — `DESIGNING_RUBRIC` is non-empty and every `Criterion` has `min_score` between 1 and 5
2. **Sample issue validity** — `CACHING_ISSUE` has non-empty `issue_title` and `issue_body`
3. **Config overrides** — `eval_config` fixture produces a `Config` with `agent.planning_model == "sonnet"`, `agent.max_turns == 50`, `agent.max_budget_usd == 2.0`

### Eval tests (`tests/evals/test_designing_eval.py`)

Run via `pytest --run-evals`. These make real LLM calls (via the local claude-code CLI) and cost real money.

1. `test_designing_produces_quality_design` — Run designing agent on sample issue against project repo worktree, judge output against designing rubric, assert all criteria pass.
2. `test_designing_revision_produces_quality_design` — Run designing with a stub `existing_design` and `feedback` (both from fixtures, not from prior test output). Uses a fresh worktree. Verify output addresses the feedback.

### Running the suite

```bash
# Eval tests only (slow, costs money, uses local claude-code CLI auth)
pytest --run-evals

# Unit tests only (fast, no LLM calls)
pytest

# Everything (unit tests + evals)
pytest --run-evals
```

### New files summary

```
tests/
  test_judge.py                     # Unit tests for judge module
  test_eval_fixtures.py             # Unit tests for rubrics and fixtures
  evals/
    __init__.py
    conftest.py                     # --run-evals flag, autouse skip fixture,
                                    # eval_config, eval_db, eval_agent_service,
                                    # eval_workspace fixtures
    judge.py                        # judge_output(), JudgeScore, JudgeResult, JudgeParseError
    fixtures/
      __init__.py
      sample_issues.py              # CACHING_ISSUE constant, STUB_DESIGN for revision test
      rubrics.py                    # Criterion dataclass, DESIGNING_RUBRIC
    test_designing_eval.py          # Eval tests for designing phase
```
