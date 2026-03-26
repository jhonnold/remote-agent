# GitHub Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous GitHub agent that polls repos for labeled issues, creates plans as draft PRs, iterates on feedback, and implements code changes via the Claude Agent SDK.

**Architecture:** Polling-based event loop with SQLite state, phase-based handlers (planning, review, implementation), and `gh` CLI for GitHub interaction. The Claude Agent SDK `query()` function drives all AI work with subagent delegation for implementation.

**Tech Stack:** Python 3.11+, claude-agent-sdk, aiosqlite, PyYAML, pytest, pytest-asyncio

---

## File Structure

| File | Responsibility |
|------|---------------|
| `pyproject.toml` | Package metadata and dependencies |
| `config.yaml` | Runtime configuration |
| `src/remote_agent/__init__.py` | Package marker |
| `src/remote_agent/models.py` | Data models: Issue, Event, AgentRun, PhaseResult, Config dataclasses |
| `src/remote_agent/config.py` | YAML config loading with validation |
| `src/remote_agent/exceptions.py` | Exception hierarchy |
| `src/remote_agent/db.py` | SQLite database: schema, CRUD, transactions |
| `src/remote_agent/github.py` | GitHub service: `gh` CLI wrapper |
| `src/remote_agent/workspace.py` | Workspace manager: clone, branch, commit, reset, cleanup |
| `src/remote_agent/prompts/planning.py` | System/user prompt builders for planning phase |
| `src/remote_agent/prompts/implementation.py` | System/user prompt builders for implementation phase |
| `src/remote_agent/prompts/review.py` | System/user prompt builders for comment interpretation |
| `src/remote_agent/agent.py` | Agent service: SDK wrapper, custom tools, subagent definitions |
| `src/remote_agent/poller.py` | GitHub poller: detects issues and comments |
| `src/remote_agent/dispatcher.py` | Event dispatcher: routes to phase handlers |
| `src/remote_agent/phases/base.py` | PhaseHandler protocol |
| `src/remote_agent/phases/planning.py` | Planning phase handler |
| `src/remote_agent/phases/plan_review.py` | Plan review phase handler |
| `src/remote_agent/phases/implementation.py` | Implementation phase handler |
| `src/remote_agent/phases/code_review.py` | Code review phase handler |
| `src/remote_agent/main.py` | Entry point: main loop |

---

### Task 1: Project Scaffold and Models

**Files:**
- Create: `pyproject.toml`
- Create: `src/remote_agent/__init__.py`
- Create: `src/remote_agent/models.py`
- Create: `src/remote_agent/exceptions.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "remote-agent"
version = "0.1.0"
requires-python = ">=3.11"
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

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Create package init files**

Create `src/remote_agent/__init__.py` (empty), `src/remote_agent/phases/__init__.py` (empty), `src/remote_agent/prompts/__init__.py` (empty).

- [ ] **Step 3: Create `tests/conftest.py`**

```python
import pytest


@pytest.fixture
def sample_issue_data():
    return {
        "number": 42,
        "title": "Add user authentication",
        "body": "We need OAuth2 support for the API.",
        "author": {"login": "myuser"},
    }
```

- [ ] **Step 4: Write failing test for models**

```python
# tests/test_models.py
from remote_agent.models import Issue, Event, AgentRun, PhaseResult


def test_issue_creation():
    issue = Issue(
        id=1,
        repo_owner="owner",
        repo_name="repo",
        issue_number=42,
        title="Test issue",
        body="Issue body",
        phase="new",
    )
    assert issue.repo_owner == "owner"
    assert issue.phase == "new"
    assert issue.plan_approved is False
    assert issue.pr_number is None


def test_event_creation():
    event = Event(
        id=1,
        issue_id=1,
        event_type="new_issue",
        payload={"number": 42, "title": "Test"},
    )
    assert event.event_type == "new_issue"
    assert event.processed is False


def test_phase_result():
    result = PhaseResult(next_phase="plan_review")
    assert result.next_phase == "plan_review"
    assert result.error_message is None


def test_agent_run_creation():
    run = AgentRun(id=1, issue_id=1, phase="planning")
    assert run.result is None
    assert run.cost_usd == 0.0
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd /home/claude/remote-agent && pip install -e ".[dev]" && pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'remote_agent'` or `ImportError`

- [ ] **Step 6: Implement models**

```python
# src/remote_agent/models.py
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Issue:
    id: int
    repo_owner: str
    repo_name: str
    issue_number: int
    title: str
    body: str | None
    phase: str
    branch_name: str | None = None
    pr_number: int | None = None
    workspace_path: str | None = None
    plan_approved: bool = False
    plan_commit_hash: str | None = None
    last_comment_id: int = 0
    budget_notified: bool = False
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class Event:
    id: int
    issue_id: int
    event_type: str
    payload: dict = field(default_factory=dict)
    processed: bool = False
    created_at: str | None = None


@dataclass
class AgentRun:
    id: int
    issue_id: int
    phase: str
    session_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    result: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error_message: str | None = None


@dataclass
class PhaseResult:
    next_phase: str
    error_message: str | None = None
```

- [ ] **Step 7: Implement exceptions**

```python
# src/remote_agent/exceptions.py
class RemoteAgentError(Exception):
    """Base exception for the remote agent system."""
    pass


class GitHubError(RemoteAgentError):
    """GitHub CLI operation failed."""
    pass


class GitError(RemoteAgentError):
    """Git operation failed."""
    pass


class AgentError(RemoteAgentError):
    """Claude Agent SDK operation failed."""
    pass


class BudgetExceededError(RemoteAgentError):
    """Daily budget limit reached."""
    pass
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: All 4 tests PASS

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml src/ tests/conftest.py tests/test_models.py
git commit -m "feat: project scaffold with models and exceptions"
```

---

### Task 2: Configuration

**Files:**
- Create: `src/remote_agent/config.py`
- Create: `config.yaml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test for config loading**

```python
# tests/test_config.py
import pytest
from pathlib import Path
from remote_agent.config import Config, load_config


def test_load_valid_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
repos:
  - owner: "testowner"
    name: "testrepo"
users:
  - "testuser"
polling:
  interval_seconds: 30
trigger:
  label: "agent"
workspace:
  base_dir: "/tmp/workspaces"
database:
  path: "data/agent.db"
agent:
  default_model: "sonnet"
  planning_model: "opus"
  implementation_model: "sonnet"
  review_model: "sonnet"
  orchestrator_model: "haiku"
  max_turns: 200
  max_budget_usd: 10.0
  daily_budget_usd: 50.0
""")
    config = load_config(str(config_file))
    assert len(config.repos) == 1
    assert config.repos[0].owner == "testowner"
    assert config.users == ["testuser"]
    assert config.polling.interval_seconds == 30
    assert config.trigger.label == "agent"
    assert config.agent.planning_model == "opus"
    # Database path should be resolved relative to config file
    assert Path(config.database.path).is_absolute()


def test_load_config_missing_required_field(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
repos: []
users: []
""")
    with pytest.raises(ValueError):
        load_config(str(config_file))


def test_load_config_empty_repos_fails(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
repos: []
users:
  - "testuser"
polling:
  interval_seconds: 30
trigger:
  label: "agent"
workspace:
  base_dir: "/tmp/workspaces"
database:
  path: "data/agent.db"
agent:
  default_model: "sonnet"
  planning_model: "opus"
  implementation_model: "sonnet"
  review_model: "sonnet"
  orchestrator_model: "haiku"
  max_turns: 200
  max_budget_usd: 10.0
  daily_budget_usd: 50.0
""")
    with pytest.raises(ValueError, match="repos"):
        load_config(str(config_file))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement config module**

```python
# src/remote_agent/config.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RepoConfig:
    owner: str
    name: str


@dataclass
class PollingConfig:
    interval_seconds: int = 60


@dataclass
class TriggerConfig:
    label: str = "agent"


@dataclass
class WorkspaceConfig:
    base_dir: str = "/home/claude/workspaces"


@dataclass
class DatabaseConfig:
    path: str = "data/agent.db"


@dataclass
class AgentConfig:
    default_model: str = "sonnet"
    planning_model: str = "opus"
    implementation_model: str = "sonnet"
    review_model: str = "sonnet"
    orchestrator_model: str = "haiku"
    max_turns: int = 200
    max_budget_usd: float = 10.0
    daily_budget_usd: float = 50.0


@dataclass
class Config:
    repos: list[RepoConfig]
    users: list[str]
    polling: PollingConfig
    trigger: TriggerConfig
    workspace: WorkspaceConfig
    database: DatabaseConfig
    agent: AgentConfig


def load_config(config_path: str) -> Config:
    """Load and validate configuration from a YAML file."""
    path = Path(config_path).resolve()
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError("Config file is empty")

    # Validate required sections
    for section in ("repos", "users", "polling", "trigger", "workspace", "database", "agent"):
        if section not in raw:
            raise ValueError(f"Missing required config section: {section}")

    repos = [RepoConfig(**r) for r in raw["repos"]]
    if not repos:
        raise ValueError("At least one repo must be configured in repos")

    users = raw["users"]
    if not users:
        raise ValueError("At least one user must be configured in users")

    # Resolve database path relative to config file
    db_path = raw["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(path.parent / db_path)

    return Config(
        repos=repos,
        users=users,
        polling=PollingConfig(**raw.get("polling", {})),
        trigger=TriggerConfig(**raw.get("trigger", {})),
        workspace=WorkspaceConfig(**raw.get("workspace", {})),
        database=DatabaseConfig(path=db_path),
        agent=AgentConfig(**raw.get("agent", {})),
    )
```

- [ ] **Step 4: Create default config.yaml**

```yaml
# config.yaml - Example configuration
repos:
  - owner: "myuser"
    name: "my-project"

users:
  - "myuser"

polling:
  interval_seconds: 60

trigger:
  label: "agent"

workspace:
  base_dir: "/home/claude/workspaces"

database:
  path: "data/agent.db"

agent:
  default_model: "sonnet"
  planning_model: "opus"
  implementation_model: "sonnet"
  review_model: "sonnet"
  orchestrator_model: "haiku"
  max_turns: 200
  max_budget_usd: 10.0
  daily_budget_usd: 50.0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/remote_agent/config.py config.yaml tests/test_config.py
git commit -m "feat: config loading with validation"
```

---

### Task 3: Database Layer

**Files:**
- Create: `src/remote_agent/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for database operations**

```python
# tests/test_db.py
import pytest
from remote_agent.db import Database


@pytest.fixture
async def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = await Database.initialize(db_path)
    yield database
    await database.close()


async def test_create_and_get_issue(db):
    issue_id = await db.create_issue(
        repo_owner="owner", repo_name="repo",
        issue_data={"number": 42, "title": "Test", "body": "Body"}
    )
    issue = await db.get_issue("owner", "repo", 42)
    assert issue is not None
    assert issue.id == issue_id
    assert issue.title == "Test"
    assert issue.phase == "new"


async def test_create_duplicate_issue_ignored(db):
    await db.create_issue("owner", "repo", {"number": 1, "title": "A", "body": ""})
    # Second create with same repo/issue should return None (already exists)
    result = await db.create_issue("owner", "repo", {"number": 1, "title": "A", "body": ""})
    assert result is None


async def test_create_and_get_event(db):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.create_event(issue_id, "new_issue", {"number": 1})
    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0].event_type == "new_issue"


async def test_mark_event_processed(db):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.create_event(issue_id, "new_issue", {})
    events = await db.get_unprocessed_events()
    await db.mark_event_processed(events[0].id)
    events = await db.get_unprocessed_events()
    assert len(events) == 0


async def test_update_issue_phase(db):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "planning")
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "planning"


async def test_get_issues_awaiting_comment(db):
    id1 = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    id2 = await db.create_issue("o", "r", {"number": 2, "title": "T2", "body": ""})
    id3 = await db.create_issue("o", "r", {"number": 3, "title": "T3", "body": ""})
    await db.update_issue_phase(id1, "plan_review")
    await db.update_issue_phase(id2, "implementing")
    await db.update_issue_phase(id3, "error")
    review_issues = await db.get_issues_awaiting_comment("o", "r")
    assert len(review_issues) == 2  # plan_review + error
    phases = {i.phase for i in review_issues}
    assert phases == {"plan_review", "error"}


async def test_create_and_complete_agent_run(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    run_id = await db.create_agent_run(issue_id, "planning")
    await db.complete_agent_run(run_id, session_id="sess-123", result="success", cost_usd=1.5)
    run = await db.get_agent_run(run_id)
    assert run.session_id == "sess-123"
    assert run.cost_usd == 1.5


async def test_get_daily_spend(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    run_id = await db.create_agent_run(issue_id, "planning")
    await db.complete_agent_run(run_id, result="success", cost_usd=5.0)
    daily = await db.get_daily_spend()
    assert daily == 5.0


async def test_transaction_for_comments(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)
    comments = [{"id": 100, "body": "LGTM"}, {"id": 101, "body": "Change X"}]
    await db.create_comment_events(issue_id, comments)
    events = await db.get_unprocessed_events()
    assert len(events) == 2
    issue = await db.get_issue("o", "r", 1)
    assert issue.last_comment_id == 101
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement database module**

```python
# src/remote_agent/db.py
from __future__ import annotations
import json
from pathlib import Path

import aiosqlite

from remote_agent.models import Issue, Event, AgentRun

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
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
    plan_commit_hash TEXT,
    last_comment_id INTEGER DEFAULT 0,
    budget_notified INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(repo_owner, repo_name, issue_number)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    processed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    phase TEXT NOT NULL,
    session_id TEXT,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    result TEXT,
    cost_usd REAL DEFAULT 0.0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    error_message TEXT
);
"""


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    @classmethod
    async def initialize(cls, db_path: str) -> Database:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await conn.executescript(SCHEMA)
        await conn.commit()
        return cls(conn)

    async def close(self):
        await self._conn.close()

    # --- Issues ---

    async def create_issue(self, repo_owner: str, repo_name: str, issue_data: dict) -> int | None:
        try:
            cursor = await self._conn.execute(
                "INSERT INTO issues (repo_owner, repo_name, issue_number, title, body) VALUES (?, ?, ?, ?, ?)",
                (repo_owner, repo_name, issue_data["number"], issue_data["title"], issue_data.get("body", "")),
            )
            await self._conn.commit()
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            return None

    async def get_issue(self, repo_owner: str, repo_name: str, issue_number: int) -> Issue | None:
        cursor = await self._conn.execute(
            "SELECT * FROM issues WHERE repo_owner = ? AND repo_name = ? AND issue_number = ?",
            (repo_owner, repo_name, issue_number),
        )
        row = await cursor.fetchone()
        return self._row_to_issue(row) if row else None

    async def get_issue_by_id(self, issue_id: int) -> Issue | None:
        cursor = await self._conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,))
        row = await cursor.fetchone()
        return self._row_to_issue(row) if row else None

    async def get_issues_awaiting_comment(self, repo_owner: str, repo_name: str) -> list[Issue]:
        cursor = await self._conn.execute(
            "SELECT * FROM issues WHERE repo_owner = ? AND repo_name = ? AND phase IN ('plan_review', 'code_review', 'error')",
            (repo_owner, repo_name),
        )
        rows = await cursor.fetchall()
        return [self._row_to_issue(r) for r in rows]

    async def get_active_issues(self) -> list[Issue]:
        cursor = await self._conn.execute(
            "SELECT * FROM issues WHERE phase IN ('planning', 'implementing')"
        )
        rows = await cursor.fetchall()
        return [self._row_to_issue(r) for r in rows]

    async def update_issue_phase(self, issue_id: int, phase: str):
        await self._conn.execute(
            "UPDATE issues SET phase = ?, updated_at = datetime('now') WHERE id = ?",
            (phase, issue_id),
        )
        await self._conn.commit()

    async def update_issue_branch(self, issue_id: int, branch: str):
        await self._conn.execute(
            "UPDATE issues SET branch_name = ?, updated_at = datetime('now') WHERE id = ?",
            (branch, issue_id),
        )
        await self._conn.commit()

    async def update_issue_pr(self, issue_id: int, pr_number: int):
        await self._conn.execute(
            "UPDATE issues SET pr_number = ?, updated_at = datetime('now') WHERE id = ?",
            (pr_number, issue_id),
        )
        await self._conn.commit()

    async def update_issue_workspace(self, issue_id: int, workspace_path: str):
        await self._conn.execute(
            "UPDATE issues SET workspace_path = ?, updated_at = datetime('now') WHERE id = ?",
            (workspace_path, issue_id),
        )
        await self._conn.commit()

    async def set_plan_approved(self, issue_id: int, approved: bool):
        await self._conn.execute(
            "UPDATE issues SET plan_approved = ?, updated_at = datetime('now') WHERE id = ?",
            (int(approved), issue_id),
        )
        await self._conn.commit()

    async def set_plan_commit_hash(self, issue_id: int, commit_hash: str):
        await self._conn.execute(
            "UPDATE issues SET plan_commit_hash = ?, updated_at = datetime('now') WHERE id = ?",
            (commit_hash, issue_id),
        )
        await self._conn.commit()

    async def update_issue_error(self, issue_id: int, error_message: str):
        await self._conn.execute(
            "UPDATE issues SET error_message = ?, updated_at = datetime('now') WHERE id = ?",
            (error_message, issue_id),
        )
        await self._conn.commit()

    async def set_budget_notified(self, issue_id: int, notified: bool):
        await self._conn.execute(
            "UPDATE issues SET budget_notified = ?, updated_at = datetime('now') WHERE id = ?",
            (int(notified), issue_id),
        )
        await self._conn.commit()

    async def update_last_comment_id(self, issue_id: int, comment_id: int):
        await self._conn.execute(
            "UPDATE issues SET last_comment_id = ?, updated_at = datetime('now') WHERE id = ?",
            (comment_id, issue_id),
        )
        await self._conn.commit()

    # --- Events ---

    async def create_event(self, issue_id: int, event_type: str, payload: dict | None = None):
        await self._conn.execute(
            "INSERT INTO events (issue_id, event_type, payload) VALUES (?, ?, ?)",
            (issue_id, event_type, json.dumps(payload or {})),
        )
        await self._conn.commit()

    async def create_comment_events(self, issue_id: int, comments: list[dict]):
        """Create events for multiple comments in a single transaction with last_comment_id update."""
        if not comments:
            return
        await self._conn.execute("BEGIN")
        try:
            for comment in comments:
                await self._conn.execute(
                    "INSERT INTO events (issue_id, event_type, payload) VALUES (?, ?, ?)",
                    (issue_id, "new_comment", json.dumps(comment)),
                )
            max_id = max(c["id"] for c in comments)
            await self._conn.execute(
                "UPDATE issues SET last_comment_id = ?, updated_at = datetime('now') WHERE id = ?",
                (max_id, issue_id),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

    async def get_unprocessed_events(self) -> list[Event]:
        cursor = await self._conn.execute(
            "SELECT * FROM events WHERE processed = 0 ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def mark_event_processed(self, event_id: int):
        await self._conn.execute("UPDATE events SET processed = 1 WHERE id = ?", (event_id,))
        await self._conn.commit()

    # --- Agent Runs ---

    async def create_agent_run(self, issue_id: int, phase: str) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO agent_runs (issue_id, phase) VALUES (?, ?)",
            (issue_id, phase),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def complete_agent_run(self, run_id: int, *, session_id: str | None = None,
                                  result: str = "success", cost_usd: float = 0.0,
                                  input_tokens: int = 0, output_tokens: int = 0,
                                  error_message: str | None = None):
        await self._conn.execute(
            """UPDATE agent_runs SET session_id = ?, completed_at = datetime('now'),
               result = ?, cost_usd = ?, input_tokens = ?, output_tokens = ?, error_message = ?
               WHERE id = ?""",
            (session_id, result, cost_usd, input_tokens, output_tokens, error_message, run_id),
        )
        await self._conn.commit()

    async def get_agent_run(self, run_id: int) -> AgentRun | None:
        cursor = await self._conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        return self._row_to_agent_run(row) if row else None

    async def get_latest_session_for_phase(self, issue_id: int, phase: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT session_id FROM agent_runs WHERE issue_id = ? AND phase = ? AND session_id IS NOT NULL ORDER BY started_at DESC LIMIT 1",
            (issue_id, phase),
        )
        row = await cursor.fetchone()
        return row["session_id"] if row else None

    async def get_daily_spend(self) -> float:
        cursor = await self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM agent_runs WHERE date(started_at) = date('now')"
        )
        row = await cursor.fetchone()
        return row["total"]

    # --- Row mappers ---

    @staticmethod
    def _row_to_issue(row) -> Issue:
        return Issue(
            id=row["id"], repo_owner=row["repo_owner"], repo_name=row["repo_name"],
            issue_number=row["issue_number"], title=row["title"], body=row["body"],
            phase=row["phase"], branch_name=row["branch_name"], pr_number=row["pr_number"],
            workspace_path=row["workspace_path"], plan_approved=bool(row["plan_approved"]),
            plan_commit_hash=row["plan_commit_hash"], last_comment_id=row["last_comment_id"],
            budget_notified=bool(row["budget_notified"]), error_message=row["error_message"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_event(row) -> Event:
        return Event(
            id=row["id"], issue_id=row["issue_id"], event_type=row["event_type"],
            payload=json.loads(row["payload"]), processed=bool(row["processed"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_agent_run(row) -> AgentRun:
        return AgentRun(
            id=row["id"], issue_id=row["issue_id"], phase=row["phase"],
            session_id=row["session_id"], started_at=row["started_at"],
            completed_at=row["completed_at"], result=row["result"],
            cost_usd=row["cost_usd"], input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"], error_message=row["error_message"],
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/db.py tests/test_db.py
git commit -m "feat: database layer with SQLite CRUD operations"
```

---

### Task 4: GitHub Service

**Files:**
- Create: `src/remote_agent/github.py`
- Test: `tests/test_github.py`

- [ ] **Step 1: Write failing tests for GitHub service**

```python
# tests/test_github.py
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.github import GitHubService
from remote_agent.exceptions import GitHubError


@pytest.fixture
def github():
    return GitHubService()


def _make_process_mock(stdout: str = "", stderr: str = "", returncode: int = 0):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.returncode = returncode
    return proc


@patch("asyncio.create_subprocess_exec")
async def test_list_issues(mock_exec, github):
    issues = [{"number": 1, "title": "Test", "body": "Body", "author": {"login": "user1"}}]
    mock_exec.return_value = _make_process_mock(stdout=json.dumps(issues))
    result = await github.list_issues("owner", "repo", "agent")
    assert len(result) == 1
    assert result[0]["number"] == 1
    mock_exec.assert_called_once()
    call_args = mock_exec.call_args[0]
    assert "gh" == call_args[0]
    assert "--label" in call_args
    assert "agent" in call_args


@patch("asyncio.create_subprocess_exec")
async def test_list_issues_gh_failure_raises(mock_exec, github):
    mock_exec.return_value = _make_process_mock(stderr="not found", returncode=1)
    with pytest.raises(GitHubError):
        await github.list_issues("owner", "repo", "agent")


@patch("asyncio.create_subprocess_exec")
async def test_get_pr_comments(mock_exec, github):
    comments = [{"id": 100, "body": "LGTM", "user": {"login": "user1"}, "created_at": "2026-01-01"}]
    mock_exec.return_value = _make_process_mock(stdout=json.dumps(comments))
    result = await github.get_pr_comments("owner", "repo", 5)
    assert len(result) == 1
    assert result[0]["id"] == 100
    assert result[0]["author"] == "user1"


@patch("asyncio.create_subprocess_exec")
async def test_create_pr_returns_number(mock_exec, github):
    mock_exec.return_value = _make_process_mock(stdout="https://github.com/owner/repo/pull/42\n")
    pr_number = await github.create_pr("owner", "repo", "Title", "Body", "branch", draft=True)
    assert pr_number == 42
    call_args = mock_exec.call_args[0]
    assert "--draft" in call_args


@patch("asyncio.create_subprocess_exec")
async def test_post_comment(mock_exec, github):
    mock_exec.return_value = _make_process_mock()
    await github.post_comment("owner", "repo", 42, "Hello")
    call_args = mock_exec.call_args[0]
    assert "comment" in call_args
    assert "42" in call_args


@patch("asyncio.create_subprocess_exec")
async def test_detect_default_branch(mock_exec, github):
    mock_exec.return_value = _make_process_mock(stdout="main\n")
    branch = await github.detect_default_branch("owner", "repo")
    assert branch == "main"
    # Second call should use cache
    branch2 = await github.detect_default_branch("owner", "repo")
    assert branch2 == "main"
    assert mock_exec.call_count == 1  # Cached
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_github.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement GitHub service**

```python
# src/remote_agent/github.py
from __future__ import annotations
import asyncio
import json

from remote_agent.exceptions import GitHubError


class GitHubService:
    def __init__(self):
        self._default_branch_cache: dict[str, str] = {}

    async def _run_gh(self, args: list[str], cwd: str | None = None) -> str:
        proc = await asyncio.create_subprocess_exec(
            "gh", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise GitHubError(f"gh {' '.join(args)} failed: {stderr.decode().strip()}")
        return stdout.decode()

    async def list_issues(self, owner: str, repo: str, label: str) -> list[dict]:
        output = await self._run_gh([
            "issue", "list",
            "--repo", f"{owner}/{repo}",
            "--label", label,
            "--state", "open",
            "--json", "number,title,body,author",
        ])
        return json.loads(output) if output.strip() else []

    async def get_pr_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        output = await self._run_gh([
            "api", f"repos/{owner}/{repo}/issues/{pr_number}/comments",
        ])
        raw_comments = json.loads(output) if output.strip() else []
        return [
            {
                "id": c["id"],
                "body": c["body"],
                "author": c["user"]["login"],
                "created_at": c["created_at"],
            }
            for c in raw_comments
        ]

    async def create_pr(self, owner: str, repo: str, title: str,
                         body: str, branch: str, draft: bool = False) -> int:
        args = [
            "pr", "create",
            "--repo", f"{owner}/{repo}",
            "--title", title,
            "--body", body,
            "--head", branch,
        ]
        if draft:
            args.append("--draft")
        output = await self._run_gh(args)
        pr_url = output.strip()
        return int(pr_url.rstrip("/").split("/")[-1])

    async def mark_pr_ready(self, owner: str, repo: str, pr_number: int) -> None:
        await self._run_gh(["pr", "ready", str(pr_number), "--repo", f"{owner}/{repo}"])

    async def mark_pr_draft(self, owner: str, repo: str, pr_number: int) -> None:
        await self._run_gh(["pr", "ready", str(pr_number), "--repo", f"{owner}/{repo}", "--undo"])

    async def post_comment(self, owner: str, repo: str, number: int, body: str) -> None:
        await self._run_gh([
            "issue", "comment", str(number),
            "--repo", f"{owner}/{repo}",
            "--body", body,
        ])

    async def clone_repo(self, owner: str, repo: str, path: str) -> None:
        await self._run_gh(["repo", "clone", f"{owner}/{repo}", path])

    async def detect_default_branch(self, owner: str, repo: str) -> str:
        key = f"{owner}/{repo}"
        if key not in self._default_branch_cache:
            output = await self._run_gh([
                "repo", "view", f"{owner}/{repo}",
                "--json", "defaultBranchRef",
                "--jq", ".defaultBranchRef.name",
            ])
            self._default_branch_cache[key] = output.strip()
        return self._default_branch_cache[key]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_github.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/github.py tests/test_github.py
git commit -m "feat: GitHub service wrapping gh CLI"
```

---

### Task 5: Workspace Manager

**Files:**
- Create: `src/remote_agent/workspace.py`
- Test: `tests/test_workspace.py`

- [ ] **Step 1: Write failing tests for workspace manager**

```python
# tests/test_workspace.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from remote_agent.workspace import WorkspaceManager
from remote_agent.config import WorkspaceConfig
from remote_agent.exceptions import GitError


@pytest.fixture
def mock_github():
    gh = AsyncMock()
    gh.clone_repo = AsyncMock()
    gh.detect_default_branch = AsyncMock(return_value="main")
    return gh


@pytest.fixture
def workspace_mgr(tmp_path, mock_github):
    config = MagicMock()
    config.workspace = WorkspaceConfig(base_dir=str(tmp_path))
    return WorkspaceManager(config, mock_github)


def test_workspace_path(workspace_mgr, tmp_path):
    path = workspace_mgr._workspace_path("owner", "repo", 42)
    assert path == tmp_path / "owner" / "repo" / "issue-42"


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_ensure_workspace_clones_new(mock_git, workspace_mgr, mock_github):
    mock_git.return_value = ""
    workspace = await workspace_mgr.ensure_workspace("owner", "repo", 42)
    mock_github.clone_repo.assert_called_once()
    assert "issue-42" in workspace


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_ensure_workspace_pulls_existing(mock_git, workspace_mgr, mock_github, tmp_path):
    # Pre-create workspace directory
    ws_path = tmp_path / "owner" / "repo" / "issue-42"
    ws_path.mkdir(parents=True)
    mock_git.return_value = ""
    workspace = await workspace_mgr.ensure_workspace("owner", "repo", 42)
    mock_github.clone_repo.assert_not_called()  # Should not clone


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_ensure_branch_creates_new(mock_git, workspace_mgr):
    mock_git.side_effect = [GitError("not found"), ""]  # checkout fails, then -b succeeds
    await workspace_mgr.ensure_branch("/tmp/ws", "agent/issue-42")
    assert mock_git.call_count == 2


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_commit_and_push_with_changes(mock_git, workspace_mgr):
    mock_git.side_effect = ["", "M file.py\n", "", ""]  # add, status, commit, push
    await workspace_mgr.commit_and_push("/tmp/ws", "branch", "message")
    assert mock_git.call_count == 4


@patch("remote_agent.workspace.WorkspaceManager._run_git")
async def test_commit_and_push_no_changes_skips_commit(mock_git, workspace_mgr):
    mock_git.side_effect = ["", "", ""]  # add, status (empty), push
    await workspace_mgr.commit_and_push("/tmp/ws", "branch", "message")
    assert mock_git.call_count == 3  # No commit call


def test_cleanup(workspace_mgr, tmp_path):
    ws_path = tmp_path / "owner" / "repo" / "issue-42"
    ws_path.mkdir(parents=True)
    (ws_path / "file.txt").write_text("test")
    workspace_mgr.cleanup("owner", "repo", 42)
    assert not ws_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement workspace manager**

```python
# src/remote_agent/workspace.py
from __future__ import annotations
import asyncio
import shutil
from pathlib import Path

from remote_agent.config import Config
from remote_agent.exceptions import GitError
from remote_agent.github import GitHubService


class WorkspaceManager:
    def __init__(self, config: Config, github: GitHubService):
        self.base_dir = Path(config.workspace.base_dir)
        self.github = github

    def _workspace_path(self, owner: str, repo: str, issue_number: int) -> Path:
        return self.base_dir / owner / repo / f"issue-{issue_number}"

    async def ensure_workspace(self, owner: str, repo: str, issue_number: int) -> str:
        path = self._workspace_path(owner, repo, issue_number)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            await self.github.clone_repo(owner, repo, str(path))
            # Set git identity for agent commits
            await self._run_git(["config", "user.name", "Remote Agent"], cwd=str(path))
            await self._run_git(["config", "user.email", "agent@localhost"], cwd=str(path))
        else:
            default_branch = await self.github.detect_default_branch(owner, repo)
            await self._run_git(["fetch", "origin"], cwd=str(path))
            await self._run_git(["checkout", default_branch], cwd=str(path))
            await self._run_git(["pull"], cwd=str(path))
        return str(path)

    async def ensure_branch(self, workspace: str, branch: str) -> None:
        try:
            await self._run_git(["checkout", branch], cwd=workspace)
            await self._run_git(["pull", "origin", branch], cwd=workspace)
        except GitError:
            await self._run_git(["checkout", "-b", branch], cwd=workspace)

    async def commit_and_push(self, workspace: str, branch: str, message: str) -> None:
        await self._run_git(["add", "-A"], cwd=workspace)
        status = await self._run_git(["status", "--porcelain"], cwd=workspace)
        if status.strip():
            await self._run_git(["commit", "-m", message], cwd=workspace)
        await self._run_git(["push", "-u", "origin", branch], cwd=workspace)

    async def get_head_commit(self, workspace: str) -> str:
        output = await self._run_git(["rev-parse", "HEAD"], cwd=workspace)
        return output.strip()

    async def reset_to_commit(self, workspace: str, commit_hash: str, branch: str) -> None:
        await self._run_git(["reset", "--hard", commit_hash], cwd=workspace)
        await self._run_git(["push", "--force", "origin", branch], cwd=workspace)

    def cleanup(self, owner: str, repo: str, issue_number: int) -> None:
        path = self._workspace_path(owner, repo, issue_number)
        if path.exists():
            shutil.rmtree(path)

    async def _run_git(self, args: list[str], cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
        return stdout.decode()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_workspace.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/workspace.py tests/test_workspace.py
git commit -m "feat: workspace manager for repo checkouts"
```

---

### Task 6: System Prompts

**Files:**
- Create: `src/remote_agent/prompts/planning.py`
- Create: `src/remote_agent/prompts/implementation.py`
- Create: `src/remote_agent/prompts/review.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write failing tests for prompt builders**

```python
# tests/test_prompts.py
from remote_agent.prompts.planning import build_planning_system_prompt, build_planning_user_prompt
from remote_agent.prompts.implementation import build_implementation_system_prompt, build_implementation_user_prompt
from remote_agent.prompts.review import build_review_system_prompt, build_review_user_prompt


def test_planning_system_prompt_contains_key_instructions():
    prompt = build_planning_system_prompt()
    assert "plan" in prompt.lower()
    assert "docs/plans/" in prompt
    assert "codebase-explorer" in prompt


def test_planning_user_prompt_new_issue():
    prompt = build_planning_user_prompt(
        issue_number=42, issue_title="Add auth", issue_body="Need OAuth2",
    )
    assert "42" in prompt
    assert "Add auth" in prompt
    assert "OAuth2" in prompt


def test_planning_user_prompt_revision():
    prompt = build_planning_user_prompt(
        issue_number=42, issue_title="Add auth", issue_body="Need OAuth2",
        existing_plan="## Old plan", feedback="Change the approach",
    )
    assert "Old plan" in prompt
    assert "Change the approach" in prompt


def test_implementation_system_prompt_contains_key_instructions():
    prompt = build_implementation_system_prompt()
    assert "implementer" in prompt
    assert "spec-reviewer" in prompt
    assert "code-reviewer" in prompt
    assert "do not write code yourself" in prompt.lower() or "do NOT write code" in prompt


def test_implementation_user_prompt():
    prompt = build_implementation_user_prompt(
        plan_content="## Task 1\nDo stuff",
        issue_title="Add auth",
    )
    assert "Task 1" in prompt
    assert "Add auth" in prompt


def test_implementation_user_prompt_with_feedback():
    prompt = build_implementation_user_prompt(
        plan_content="## Task 1", issue_title="X",
        feedback="Fix the error handling",
    )
    assert "Fix the error handling" in prompt


def test_review_system_prompt():
    prompt = build_review_system_prompt()
    assert "classify_comment" in prompt


def test_review_user_prompt_plan_review():
    prompt = build_review_user_prompt(
        comment="Looks good!", context="plan_review", issue_title="Add auth",
    )
    assert "Looks good!" in prompt
    assert "back_to_planning" not in prompt  # Not valid for plan_review


def test_review_user_prompt_code_review():
    prompt = build_review_user_prompt(
        comment="Go back to planning", context="code_review", issue_title="Add auth",
    )
    assert "back_to_planning" in prompt  # Valid for code_review
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prompts.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement planning prompts**

```python
# src/remote_agent/prompts/planning.py

def build_planning_system_prompt() -> str:
    return """You are an expert software architect creating implementation plans.

## Your Task
Read the GitHub issue, explore the codebase thoroughly, and create a detailed implementation plan.

## Process
1. **Understand the request**: Read the issue carefully. Identify what is being asked.
2. **Explore the codebase**: Use the codebase-explorer agent to understand:
   - Project structure and conventions
   - Relevant existing code
   - Testing patterns and dependencies
3. **Design the solution**: Think through the architecture before writing anything.
4. **Write the plan**: Create a detailed plan document.

## Plan Document Format
Write the plan to the path specified in the user prompt with this structure:

```markdown
# [Feature/Fix Name] Implementation Plan

**Issue:** #<number>
**Goal:** [One sentence describing what this achieves]
**Architecture:** [2-3 sentences about the approach]

## Tasks

### Task 1: [Component/Change Name]
**Files:**
- Create: `exact/path/file.py`
- Modify: `exact/path/file.py`
- Test: `tests/exact/path/test_file.py`

**Steps:**
1. Write failing test: [describe what to test and provide code]
2. Implement: [describe the implementation and provide code]
3. Verify: [exact test command]

### Task 2: ...
(continue for each task)

## Testing Strategy
[How to verify the complete implementation]

## Risks and Considerations
[Any edge cases, breaking changes, or concerns]
```

## Rules
- Each task should be independently implementable (2-5 minutes of work)
- Follow test-driven development: every task starts with a failing test
- Follow existing codebase patterns and conventions
- Be specific: exact file paths, function signatures, test commands
- Do NOT implement anything. Only create the plan document.
- If this is a revision, incorporate the feedback while preserving approved parts.
"""


def build_planning_user_prompt(
    issue_number: int,
    issue_title: str,
    issue_body: str,
    existing_plan: str | None = None,
    feedback: str | None = None,
) -> str:
    parts = [f"Create a plan for the following GitHub issue.\n"]
    parts.append(f"**Issue #{issue_number}: {issue_title}**\n\n{issue_body}\n")
    parts.append(f"Write the plan to: `docs/plans/issue-{issue_number}-plan.md`\n")

    if existing_plan and feedback:
        parts.append("\n---\n## Revision Request\n")
        parts.append(f"The previous plan needs revision based on this feedback:\n\n")
        parts.append(f"**Feedback:** {feedback}\n\n")
        parts.append(f"**Previous plan:**\n\n{existing_plan}\n")
        parts.append("\nRevise the plan to address the feedback. Keep parts that were not criticized.\n")
    elif existing_plan:
        parts.append(f"\n**Previous plan (for reference):**\n\n{existing_plan}\n")

    return "\n".join(parts)
```

- [ ] **Step 4: Implement implementation prompts**

```python
# src/remote_agent/prompts/implementation.py

def build_implementation_system_prompt() -> str:
    return """You are a senior developer implementing a plan using subagents.

## Your Role
You are the orchestrator. You read the plan document, then dispatch subagents to implement each task. You do NOT write code yourself - you delegate to subagents and review their work.

## Process
For each task in the plan:

### Step 1: Dispatch Implementer
Use the `implementer` agent with a prompt that includes:
- The full task text (copy it entirely, do not reference the plan file)
- Context about where this task fits in the overall plan
- Any dependencies on previously completed tasks
- Specific file paths and test commands from the plan

### Step 2: Review Spec Compliance
After the implementer reports completion, use the `spec-reviewer` agent to verify:
- The implementation matches exactly what was requested in the task
- Nothing extra was added
- Nothing was missed
- Tests exist and pass

If issues are found, send the implementer back to fix them. Maximum 3 iterations per task.

### Step 3: Review Code Quality
After spec compliance passes, use the `code-reviewer` agent to verify:
- Code is clean and maintainable
- Tests are meaningful
- Follows existing codebase patterns

If issues are found, send the implementer back to fix them. Maximum 3 iterations per task.

### Step 4: Move to Next Task
Mark the task complete and proceed to the next one.

## Rules
- Execute tasks in order. Do not parallelize implementer subagents.
- Always do spec review BEFORE code quality review.
- Do not skip reviews.
- If an implementer is blocked after 3 review iterations, stop and report the issue.
- After all tasks are complete, run the full test suite to verify everything works together.
- If this is a code revision based on feedback, focus on the specific changes requested.
"""


def build_implementation_user_prompt(
    plan_content: str,
    issue_title: str,
    feedback: str | None = None,
) -> str:
    parts = [f"Implement the following plan for: **{issue_title}**\n\n"]
    parts.append(f"## Plan\n\n{plan_content}\n")

    if feedback:
        parts.append("\n---\n## Revision Request\n")
        parts.append(f"The reviewer has requested changes:\n\n**Feedback:** {feedback}\n\n")
        parts.append("Focus on addressing this specific feedback.\n")

    return "\n".join(parts)
```

- [ ] **Step 5: Implement review prompts**

```python
# src/remote_agent/prompts/review.py

def build_review_system_prompt() -> str:
    return """You are interpreting a human's comment on a pull request.

## Your Task
Read the comment and classify the human's intent using the classify_comment tool.

## Intent Categories
- **approve**: The human is satisfied and wants to proceed to the next phase.
  Examples: "looks good", "approved", "LGTM", "ship it", "go ahead"
- **revise**: The human wants changes to the current work.
  Examples: "change X to Y", "this won't work because...", "also handle edge case Z"
- **question**: The human is asking a question and expects an answer, not action.
  Examples: "why did you choose X?", "what happens if Z?", "can you explain this?"
- **back_to_planning**: The human wants to rethink the approach entirely (only valid during code review).
  Examples: "the plan needs to change", "let's rethink", "go back to planning"

## Rules
- When uncertain, default to "revise" (safer than proceeding on a misread approval).
- For "question" intent, include a helpful response in the response field.
- For "revise" intent, include the revision request summary in the response field.
- Be conservative with "approve" - only when the intent is clearly positive.

Call the classify_comment tool with your classification.
"""


def build_review_user_prompt(
    comment: str,
    context: str,
    issue_title: str,
) -> str:
    if context == "plan_review":
        valid_intents = "approve, revise, question"
    elif context == "code_review":
        valid_intents = "approve, revise, question, back_to_planning"
    else:
        valid_intents = "approve, revise, question"

    return f"""Classify the following comment on the PR for: **{issue_title}**

**Valid intents for this phase ({context}):** {valid_intents}

**Comment:**
{comment}

Call the classify_comment tool with your classification.
"""
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_prompts.py -v`
Expected: All 9 tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/remote_agent/prompts/ tests/test_prompts.py
git commit -m "feat: system prompts for planning, implementation, and review"
```

---

### Task 7: Agent Service

**Files:**
- Create: `src/remote_agent/agent.py`
- Test: `tests/test_agent.py`

- [ ] **Step 1: Write failing tests for agent service**

```python
# tests/test_agent.py
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.agent import AgentService, CommentInterpretation
from remote_agent.config import AgentConfig


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.agent = AgentConfig()
    return config


@pytest.fixture
async def mock_db(tmp_path):
    from remote_agent.db import Database
    db = await Database.initialize(str(tmp_path / "test.db"))
    yield db
    await db.close()


@pytest.fixture
def agent_service(mock_config, mock_db):
    return AgentService(mock_config, mock_db)


def test_comment_interpretation_dataclass():
    interp = CommentInterpretation(intent="approve", response="Plan approved.")
    assert interp.intent == "approve"
    assert interp.response == "Plan approved."


def test_get_planning_subagents(agent_service):
    agents = agent_service._get_planning_subagents()
    assert "codebase-explorer" in agents


def test_get_implementation_subagents(agent_service):
    agents = agent_service._get_implementation_subagents()
    assert "implementer" in agents
    assert "spec-reviewer" in agents
    assert "code-reviewer" in agents


def test_parse_interpretation_valid(agent_service):
    result_text = json.dumps({"intent": "approve", "response": "Looks good"})
    interp = agent_service._parse_interpretation(result_text)
    assert interp.intent == "approve"


def test_parse_interpretation_invalid_defaults_to_revise(agent_service):
    interp = agent_service._parse_interpretation("unparseable garbage")
    assert interp.intent == "revise"


def test_parse_interpretation_none_defaults_to_revise(agent_service):
    interp = agent_service._parse_interpretation(None)
    assert interp.intent == "revise"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement agent service**

```python
# src/remote_agent/agent.py
from __future__ import annotations
import json
import logging
from dataclasses import dataclass

from remote_agent.config import Config
from remote_agent.db import Database
from remote_agent.exceptions import AgentError
from remote_agent.prompts.planning import build_planning_system_prompt, build_planning_user_prompt
from remote_agent.prompts.implementation import build_implementation_system_prompt, build_implementation_user_prompt
from remote_agent.prompts.review import build_review_system_prompt, build_review_user_prompt

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    success: bool
    session_id: str | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    result_text: str | None = None
    error: str | None = None


@dataclass
class CommentInterpretation:
    intent: str  # "approve", "revise", "question", "back_to_planning"
    response: str | None = None


class AgentService:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    async def run_planning(self, *, issue_number: int, issue_title: str,
                            issue_body: str, cwd: str, issue_id: int,
                            existing_plan: str | None = None,
                            feedback: str | None = None) -> AgentResult:
        from claude_agent_sdk import query, ClaudeAgentOptions

        system_prompt = build_planning_system_prompt()
        user_prompt = build_planning_user_prompt(
            issue_number=issue_number, issue_title=issue_title,
            issue_body=issue_body, existing_plan=existing_plan, feedback=feedback,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Glob", "Grep", "Write", "Edit", "Bash", "WebSearch", "Agent"],
            permission_mode="bypassPermissions",
            model=self.config.agent.planning_model,
            max_turns=self.config.agent.max_turns,
            max_budget_usd=self.config.agent.max_budget_usd,
            cwd=cwd,
            agents=self._get_planning_subagents(),
        )
        return await self._run_query(user_prompt, options, issue_id, phase="planning", allow_resume=True)

    async def run_implementation(self, *, plan_content: str, issue_title: str,
                                  cwd: str, issue_id: int,
                                  feedback: str | None = None) -> AgentResult:
        from claude_agent_sdk import query, ClaudeAgentOptions

        system_prompt = build_implementation_system_prompt()
        user_prompt = build_implementation_user_prompt(
            plan_content=plan_content, issue_title=issue_title, feedback=feedback,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Glob", "Grep", "Bash", "Agent"],
            permission_mode="bypassPermissions",
            model=self.config.agent.orchestrator_model,
            max_turns=self.config.agent.max_turns,
            max_budget_usd=self.config.agent.max_budget_usd,
            cwd=cwd,
            agents=self._get_implementation_subagents(),
        )
        return await self._run_query(user_prompt, options, issue_id, phase="implementing", allow_resume=True)

    async def interpret_comment(self, *, comment: str, context: str,
                                 issue_title: str, issue_id: int) -> CommentInterpretation:
        from claude_agent_sdk import query, ClaudeAgentOptions, tool, create_sdk_mcp_server

        @tool("classify_comment", "Classify a PR comment's intent and provide a response",
              {"intent": str, "response": str})
        async def classify_comment(args):
            return {"content": [{"type": "text", "text": json.dumps(args)}]}

        review_server = create_sdk_mcp_server(
            name="review", version="1.0.0", tools=[classify_comment],
        )

        system_prompt = build_review_system_prompt()
        user_prompt = build_review_user_prompt(
            comment=comment, context=context, issue_title=issue_title,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"review": review_server},
            allowed_tools=["mcp__review__classify_comment"],
            permission_mode="bypassPermissions",
            model=self.config.agent.review_model,
            max_turns=1,
            max_budget_usd=0.50,
            cwd="/tmp",
        )
        result = await self._run_query(user_prompt, options, issue_id, phase="review")
        return self._parse_interpretation(result.result_text)

    async def _run_query(self, prompt: str, options, issue_id: int, phase: str,
                          allow_resume: bool = False) -> AgentResult:
        from claude_agent_sdk import query, ResultMessage

        run_id = await self.db.create_agent_run(issue_id, phase)

        # Support session resumption on retry
        if allow_resume:
            prev_session = await self.db.get_latest_session_for_phase(issue_id, phase)
            if prev_session:
                options.resume = prev_session
                logger.info("Resuming session %s for issue %d phase %s", prev_session, issue_id, phase)

        session_id = None
        result_text = None
        cost = 0.0
        input_tokens = 0
        output_tokens = 0

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    session_id = message.session_id
                    result_text = message.result
                    cost = message.total_cost_usd or 0.0
                    usage = message.usage or {}
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)

            await self.db.complete_agent_run(
                run_id, session_id=session_id, result="success",
                cost_usd=cost, input_tokens=input_tokens, output_tokens=output_tokens,
            )
            return AgentResult(
                success=True, session_id=session_id, cost_usd=cost,
                input_tokens=input_tokens, output_tokens=output_tokens,
                result_text=result_text,
            )
        except Exception as e:
            await self.db.complete_agent_run(
                run_id, result="error", cost_usd=cost,
                input_tokens=input_tokens, output_tokens=output_tokens,
                error_message=str(e),
            )
            raise AgentError(str(e)) from e

    def _get_planning_subagents(self) -> dict:
        from claude_agent_sdk import AgentDefinition
        return {
            "codebase-explorer": AgentDefinition(
                description="Explores the codebase to understand structure, patterns, and conventions. Use this to research the repo before creating the plan.",
                prompt="You are a codebase exploration specialist. Analyze the code structure, find patterns, understand conventions, and report findings clearly and concisely. Focus on: project structure, testing patterns, key abstractions, and coding style.",
                tools=["Read", "Glob", "Grep"],
                model="haiku",
            ),
        }

    def _get_implementation_subagents(self) -> dict:
        from claude_agent_sdk import AgentDefinition
        return {
            "implementer": AgentDefinition(
                description="Implements a specific task from the plan. Use for each individual implementation task.",
                prompt="""You are a skilled developer implementing a specific task. You will receive the full task description including files to create/modify, tests to write, and implementation details.

## Process
1. Read the task carefully
2. Write the failing test first
3. Run it to verify it fails
4. Write the minimal implementation to pass
5. Run tests to verify they pass
6. Self-review your work

## Rules
- Follow the task instructions exactly
- Use test-driven development
- Follow existing codebase patterns
- Do not modify files outside the task scope
- Run tests after every change

## Report
When done, report:
- Status: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- What you implemented
- Tests written and their results
- Files changed
- Any concerns or issues found during self-review
""",
                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                model="sonnet",
            ),
            "spec-reviewer": AgentDefinition(
                description="Reviews implementation for spec compliance. Use after each task is implemented.",
                prompt="""You are a spec compliance reviewer. Your job is to verify that the implementation exactly matches what was requested.

## What to Check
- Every requirement in the task is implemented
- Nothing extra was added (YAGNI)
- Nothing was missed or misunderstood
- Tests exist and test the right things
- Code matches the file paths specified in the task

## CRITICAL
Do NOT trust the implementer's report. Read the actual code and tests yourself.

## Output
- APPROVED: Implementation matches spec exactly
- ISSUES FOUND: List specific issues with file:line references
""",
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
            "code-reviewer": AgentDefinition(
                description="Reviews code quality after spec compliance passes. Use after spec-reviewer approves.",
                prompt="""You are a code quality reviewer. The implementation has already passed spec compliance review. Now verify it is well-built.

## What to Check
- Code is clean and readable
- Tests are meaningful (not just coverage padding)
- Follows existing codebase patterns and conventions
- No security issues
- Error handling is appropriate
- File decomposition is correct (one responsibility per file)

## Output
- APPROVED: Code quality is good
- ISSUES FOUND: List specific issues with file:line references, categorized as Critical/Important/Minor
""",
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
        }

    def _parse_interpretation(self, result_text: str | None) -> CommentInterpretation:
        if not result_text:
            return CommentInterpretation(intent="revise", response="Could not interpret comment.")
        try:
            # Try to extract JSON from the result text
            # The classify_comment tool returns JSON, but the result may have surrounding text
            for line in result_text.split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    data = json.loads(line)
                    intent = data.get("intent", "revise")
                    if intent not in ("approve", "revise", "question", "back_to_planning"):
                        intent = "revise"
                    return CommentInterpretation(
                        intent=intent,
                        response=data.get("response"),
                    )
            # Try parsing the whole thing
            data = json.loads(result_text)
            intent = data.get("intent", "revise")
            if intent not in ("approve", "revise", "question", "back_to_planning"):
                intent = "revise"
            return CommentInterpretation(intent=intent, response=data.get("response"))
        except (json.JSONDecodeError, KeyError, TypeError):
            return CommentInterpretation(intent="revise", response="Could not interpret comment.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/agent.py tests/test_agent.py
git commit -m "feat: agent service wrapping Claude Agent SDK"
```

---

### Task 8: Poller Service

**Files:**
- Create: `src/remote_agent/poller.py`
- Test: `tests/test_poller.py`

- [ ] **Step 1: Write failing tests for poller**

```python
# tests/test_poller.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from remote_agent.poller import Poller
from remote_agent.config import Config, RepoConfig, PollingConfig, TriggerConfig


@pytest.fixture
def mock_config():
    config = MagicMock(spec=Config)
    config.repos = [RepoConfig(owner="owner", name="repo")]
    config.users = ["testuser"]
    config.polling = PollingConfig(interval_seconds=60)
    config.trigger = TriggerConfig(label="agent")
    return config


@pytest.fixture
async def db(tmp_path):
    from remote_agent.db import Database
    database = await Database.initialize(str(tmp_path / "test.db"))
    yield database
    await database.close()


@pytest.fixture
def mock_github():
    return AsyncMock()


@pytest.fixture
def poller(mock_config, db, mock_github):
    return Poller(mock_config, db, mock_github)


async def test_poll_new_issue_creates_event(poller, db, mock_github):
    mock_github.list_issues.return_value = [
        {"number": 1, "title": "Test", "body": "Body", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0].event_type == "new_issue"


async def test_poll_ignores_non_allowlisted_user(poller, db, mock_github):
    mock_github.list_issues.return_value = [
        {"number": 1, "title": "Test", "body": "Body", "author": {"login": "stranger"}}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 0


async def test_poll_ignores_already_tracked_issue(poller, db, mock_github):
    mock_github.list_issues.return_value = [
        {"number": 1, "title": "Test", "body": "Body", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    # Second poll should not create duplicate
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    assert len(events) == 1


async def test_poll_detects_new_pr_comments(poller, db, mock_github):
    # Create issue in plan_review phase
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "LGTM", "author": "testuser", "created_at": "2026-01-01"}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 1


async def test_poll_filters_agent_own_comments(poller, db, mock_github):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "plan_review")
    await db.update_issue_pr(issue_id, 10)

    mock_github.list_issues.return_value = [
        {"number": 1, "title": "T", "body": "", "author": {"login": "testuser"}}
    ]
    mock_github.get_pr_comments.return_value = [
        {"id": 100, "body": "Plan created.", "author": "bot-user", "created_at": "2026-01-01"}
    ]
    await poller.poll_once()
    events = await db.get_unprocessed_events()
    comment_events = [e for e in events if e.event_type == "new_comment"]
    assert len(comment_events) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_poller.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement poller**

```python
# src/remote_agent/poller.py
from __future__ import annotations
import logging

from remote_agent.config import Config
from remote_agent.db import Database
from remote_agent.github import GitHubService

logger = logging.getLogger(__name__)


class Poller:
    def __init__(self, config: Config, db: Database, github: GitHubService):
        self.config = config
        self.db = db
        self.github = github

    async def poll_once(self):
        for repo in self.config.repos:
            try:
                await self._poll_repo(repo.owner, repo.name)
            except Exception:
                logger.exception("Error polling %s/%s", repo.owner, repo.name)

    async def _poll_repo(self, owner: str, name: str):
        # 1. Check for new issues
        issues = await self.github.list_issues(owner, name, self.config.trigger.label)
        for issue_data in issues:
            author = issue_data.get("author", {}).get("login", "")
            if author not in self.config.users:
                continue

            existing = await self.db.get_issue(owner, name, issue_data["number"])
            if not existing:
                issue_id = await self.db.create_issue(owner, name, issue_data)
                if issue_id:
                    await self.db.create_event(issue_id, "new_issue", issue_data)
                    logger.info("New issue detected: %s/%s#%d", owner, name, issue_data["number"])
            elif existing.phase in ("completed", "error"):
                # Issue reappeared with label after being completed/errored - reopen
                await self.db.create_event(existing.id, "reopen", issue_data)
                logger.info("Reopened issue: %s/%s#%d", owner, name, issue_data["number"])

        # 2. Check for new PR comments on issues in review or error phases
        review_issues = await self.db.get_issues_awaiting_comment(owner, name)
        for issue in review_issues:
            if not issue.pr_number:
                continue
            try:
                comments = await self.github.get_pr_comments(owner, name, issue.pr_number)
            except Exception:
                logger.exception("Error fetching comments for PR #%d", issue.pr_number)
                continue

            new_comments = [c for c in comments if c["id"] > issue.last_comment_id]
            new_comments = [c for c in new_comments if c["author"] in self.config.users]

            if new_comments:
                await self.db.create_comment_events(issue.id, new_comments)
                logger.info("New comments on %s/%s PR#%d: %d",
                           owner, name, issue.pr_number, len(new_comments))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_poller.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/poller.py tests/test_poller.py
git commit -m "feat: poller service for GitHub issue and comment detection"
```

---

### Task 9: Phase Handlers

**Files:**
- Create: `src/remote_agent/phases/base.py`
- Create: `src/remote_agent/phases/planning.py`
- Create: `src/remote_agent/phases/plan_review.py`
- Create: `src/remote_agent/phases/implementation.py`
- Create: `src/remote_agent/phases/code_review.py`
- Test: `tests/test_phases/test_planning.py`
- Test: `tests/test_phases/test_plan_review.py`
- Test: `tests/test_phases/test_implementation.py`
- Test: `tests/test_phases/test_code_review.py`

- [ ] **Step 1: Create base.py**

```python
# src/remote_agent/phases/base.py
from __future__ import annotations
from typing import Protocol

from remote_agent.models import Issue, Event, PhaseResult


class PhaseHandler(Protocol):
    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        """Handle an event for the given issue and return the next phase."""
        ...
```

- [ ] **Step 2: Write failing test for planning handler**

```python
# tests/test_phases/__init__.py  (empty)

# tests/test_phases/test_planning.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from remote_agent.phases.planning import PlanningHandler
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
    return PlanningHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])


@pytest.fixture
def new_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="Need OAuth2", phase="new")


@pytest.fixture
def new_issue_event():
    return Event(id=1, issue_id=1, event_type="new_issue",
                 payload={"number": 42, "title": "Add auth", "body": "Need OAuth2"})


async def test_planning_creates_branch_and_pr(handler, deps, new_issue, new_issue_event):
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )
    deps["github"].create_pr.return_value = 10

    result = await handler.handle(new_issue, new_issue_event)

    assert result.next_phase == "plan_review"
    deps["workspace_mgr"].ensure_branch.assert_called_once()
    deps["github"].create_pr.assert_called_once()
    deps["db"].update_issue_pr.assert_called_once_with(1, 10)
    deps["db"].set_plan_commit_hash.assert_called_once()


async def test_planning_revision_reuses_existing_pr(handler, deps, new_issue_event):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="planning",
                  pr_number=10, branch_name="agent/issue-42")
    event = Event(id=2, issue_id=1, event_type="revision_requested",
                  payload={"body": "Change approach"})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "def456"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-2", cost_usd=0.5, input_tokens=50, output_tokens=100,
    )

    result = await handler.handle(issue, event)

    assert result.next_phase == "plan_review"
    deps["github"].create_pr.assert_not_called()  # PR already exists
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_phases/test_planning.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 4: Implement planning handler**

```python
# src/remote_agent/phases/planning.py
from __future__ import annotations
import logging
from pathlib import Path

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class PlanningHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        workspace = await self.workspace_mgr.ensure_workspace(
            issue.repo_owner, issue.repo_name, issue.issue_number,
        )
        await self.db.update_issue_workspace(issue.id, workspace)

        branch = issue.branch_name or f"agent/issue-{issue.issue_number}"
        await self.workspace_mgr.ensure_branch(workspace, branch)
        await self.db.update_issue_branch(issue.id, branch)

        # Read existing plan if revision
        existing_plan = None
        plan_path = Path(workspace) / "docs" / "plans" / f"issue-{issue.issue_number}-plan.md"
        if plan_path.exists():
            existing_plan = plan_path.read_text()

        feedback = event.payload.get("body") if event.event_type in ("revision_requested", "new_comment") else None

        await self.agent_service.run_planning(
            issue_number=issue.issue_number,
            issue_title=issue.title,
            issue_body=issue.body or "",
            cwd=workspace,
            issue_id=issue.id,
            existing_plan=existing_plan,
            feedback=feedback,
        )

        commit_msg = "docs: plan for issue #{}".format(issue.issue_number)
        if existing_plan:
            commit_msg = "docs: revise plan for issue #{}".format(issue.issue_number)
        await self.workspace_mgr.commit_and_push(workspace, branch, commit_msg)

        plan_commit = await self.workspace_mgr.get_head_commit(workspace)
        await self.db.set_plan_commit_hash(issue.id, plan_commit)

        pr_number = issue.pr_number
        if not pr_number:
            pr_number = await self.github.create_pr(
                issue.repo_owner, issue.repo_name,
                title=f"[Agent] Plan for: {issue.title}",
                body=f"Plan for #{issue.issue_number}. Review the plan file and comment with feedback.",
                branch=branch, draft=True,
            )
            await self.db.update_issue_pr(issue.id, pr_number)

        await self.github.post_comment(
            issue.repo_owner, issue.repo_name, pr_number,
            "Plan created/updated. Please review the plan file and comment with your feedback.",
        )

        return PhaseResult(next_phase="plan_review")
```

- [ ] **Step 5: Write failing test for plan review handler**

```python
# tests/test_phases/test_plan_review.py
import pytest
from unittest.mock import AsyncMock
from remote_agent.phases.plan_review import PlanReviewHandler
from remote_agent.models import Issue, Event
from remote_agent.agent import CommentInterpretation


@pytest.fixture
def deps():
    return {"db": AsyncMock(), "github": AsyncMock(), "agent_service": AsyncMock()}


@pytest.fixture
def handler(deps):
    return PlanReviewHandler(deps["db"], deps["github"], deps["agent_service"])


@pytest.fixture
def review_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="", phase="plan_review", pr_number=10)


async def test_approve_transitions_to_implementing(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "implementing"
    deps["db"].set_plan_approved.assert_called_once_with(1, True)
    # Must create event to drive implementation handler
    deps["db"].create_event.assert_called_once()


async def test_revise_creates_event_and_stays(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Change X"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="revise")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "planning"
    deps["db"].create_event.assert_called_once()


async def test_question_posts_response_and_stays(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Why X?"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(
        intent="question", response="Because Y.")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "plan_review"
    deps["github"].post_comment.assert_called_once()
```

- [ ] **Step 6: Implement plan review handler**

```python
# src/remote_agent/phases/plan_review.py
from __future__ import annotations
import logging

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService

logger = logging.getLogger(__name__)


class PlanReviewHandler:
    def __init__(self, db: Database, github: GitHubService, agent_service: AgentService):
        self.db = db
        self.github = github
        self.agent_service = agent_service

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        comment_body = event.payload.get("body", "")

        interpretation = await self.agent_service.interpret_comment(
            comment=comment_body, context="plan_review",
            issue_title=issue.title, issue_id=issue.id,
        )
        logger.info("Plan review comment interpreted as: %s", interpretation.intent)

        if interpretation.intent == "approve":
            await self.db.set_plan_approved(issue.id, True)
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.pr_number,
                "Plan approved. Starting implementation...",
            )
            # Create event to drive the implementation handler
            await self.db.create_event(issue.id, "revision_requested", {})
            return PhaseResult(next_phase="implementing")

        elif interpretation.intent == "revise":
            await self.db.create_event(issue.id, "revision_requested", event.payload)
            return PhaseResult(next_phase="planning")

        elif interpretation.intent == "question":
            response = interpretation.response or "I'll look into that."
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.pr_number, response,
            )
            return PhaseResult(next_phase="plan_review")

        return PhaseResult(next_phase="plan_review")
```

- [ ] **Step 7: Write failing test for implementation handler**

```python
# tests/test_phases/test_implementation.py
import pytest
from unittest.mock import AsyncMock
from remote_agent.phases.implementation import ImplementationHandler
from remote_agent.models import Issue, Event
from remote_agent.agent import AgentResult


@pytest.fixture
def deps():
    return {"db": AsyncMock(), "github": AsyncMock(), "agent_service": AsyncMock(), "workspace_mgr": AsyncMock()}


@pytest.fixture
def handler(deps):
    return ImplementationHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])


@pytest.fixture
def impl_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="", phase="implementing",
                 pr_number=10, branch_name="agent/issue-42",
                 workspace_path="/tmp/ws")


async def test_implementation_publishes_pr(handler, deps, impl_issue):
    event = Event(id=1, issue_id=1, event_type="revision_requested", payload={})
    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["agent_service"].run_implementation.return_value = AgentResult(
        success=True, session_id="s", cost_usd=2.0, input_tokens=500, output_tokens=1000,
    )
    # Mock reading the plan file
    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.exists", lambda self: True)
        m.setattr("pathlib.Path.read_text", lambda self: "## Plan content")
        result = await handler.handle(impl_issue, event)

    assert result.next_phase == "code_review"
    deps["github"].mark_pr_ready.assert_called_once()
```

- [ ] **Step 8: Implement implementation handler**

```python
# src/remote_agent/phases/implementation.py
from __future__ import annotations
import logging
from pathlib import Path

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class ImplementationHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        workspace = await self.workspace_mgr.ensure_workspace(
            issue.repo_owner, issue.repo_name, issue.issue_number,
        )
        await self.workspace_mgr.ensure_branch(workspace, issue.branch_name)

        plan_path = Path(workspace) / "docs" / "plans" / f"issue-{issue.issue_number}-plan.md"
        if not plan_path.exists():
            return PhaseResult(next_phase="error", error_message="Plan file not found")
        plan_content = plan_path.read_text()

        feedback = event.payload.get("body") if event.event_type in ("revision_requested", "new_comment") else None

        await self.agent_service.run_implementation(
            plan_content=plan_content,
            issue_title=issue.title,
            cwd=workspace,
            issue_id=issue.id,
            feedback=feedback,
        )

        commit_msg = f"feat: implement plan for issue #{issue.issue_number}"
        if feedback:
            commit_msg = f"fix: address review feedback for issue #{issue.issue_number}"
        await self.workspace_mgr.commit_and_push(workspace, issue.branch_name, commit_msg)

        await self.github.mark_pr_ready(issue.repo_owner, issue.repo_name, issue.pr_number)

        await self.github.post_comment(
            issue.repo_owner, issue.repo_name, issue.pr_number,
            "Implementation complete. Please review the code and comment with feedback.",
        )

        return PhaseResult(next_phase="code_review")
```

- [ ] **Step 9: Write failing test for code review handler**

```python
# tests/test_phases/test_code_review.py
import pytest
from unittest.mock import AsyncMock
from remote_agent.phases.code_review import CodeReviewHandler
from remote_agent.models import Issue, Event
from remote_agent.agent import CommentInterpretation


@pytest.fixture
def deps():
    return {"db": AsyncMock(), "github": AsyncMock(), "agent_service": AsyncMock(), "workspace_mgr": AsyncMock()}


@pytest.fixture
def handler(deps):
    return CodeReviewHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])


@pytest.fixture
def review_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="", phase="code_review",
                 pr_number=10, branch_name="agent/issue-42",
                 plan_commit_hash="abc123")


async def test_approve_completes(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "completed"
    deps["workspace_mgr"].cleanup.assert_called_once()


async def test_revise_creates_event(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Fix errors"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="revise")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "implementing"
    deps["db"].create_event.assert_called_once()


async def test_back_to_planning_resets_state(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Rethink approach"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="back_to_planning")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "planning"
    deps["db"].set_plan_approved.assert_called_once_with(1, False)
    deps["github"].mark_pr_draft.assert_called_once()
    deps["workspace_mgr"].reset_to_commit.assert_called_once()
    deps["db"].create_event.assert_called_once()
```

- [ ] **Step 10: Implement code review handler**

```python
# src/remote_agent/phases/code_review.py
from __future__ import annotations
import logging

from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class CodeReviewHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        comment_body = event.payload.get("body", "")

        interpretation = await self.agent_service.interpret_comment(
            comment=comment_body, context="code_review",
            issue_title=issue.title, issue_id=issue.id,
        )
        logger.info("Code review comment interpreted as: %s", interpretation.intent)

        if interpretation.intent == "approve":
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.pr_number,
                "Code approved! The PR is ready for you to merge.",
            )
            self.workspace_mgr.cleanup(issue.repo_owner, issue.repo_name, issue.issue_number)
            return PhaseResult(next_phase="completed")

        elif interpretation.intent == "revise":
            await self.db.create_event(issue.id, "revision_requested", event.payload)
            return PhaseResult(next_phase="implementing")

        elif interpretation.intent == "back_to_planning":
            await self.db.set_plan_approved(issue.id, False)
            await self.github.mark_pr_draft(issue.repo_owner, issue.repo_name, issue.pr_number)
            if issue.plan_commit_hash:
                await self.workspace_mgr.reset_to_commit(
                    issue.workspace_path, issue.plan_commit_hash, issue.branch_name,
                )
            await self.db.create_event(issue.id, "revision_requested", event.payload)
            return PhaseResult(next_phase="planning")

        elif interpretation.intent == "question":
            response = interpretation.response or "I'll look into that."
            await self.github.post_comment(
                issue.repo_owner, issue.repo_name, issue.pr_number, response,
            )
            return PhaseResult(next_phase="code_review")

        return PhaseResult(next_phase="code_review")
```

- [ ] **Step 11: Run all phase handler tests**

Run: `pytest tests/test_phases/ -v`
Expected: All tests PASS

- [ ] **Step 12: Commit**

```bash
git add src/remote_agent/phases/ tests/test_phases/
git commit -m "feat: phase handlers for planning, review, implementation, and code review"
```

---

### Task 10: Dispatcher

**Files:**
- Create: `src/remote_agent/dispatcher.py`
- Test: `tests/test_dispatcher.py`

- [ ] **Step 1: Write failing tests for dispatcher**

```python
# tests/test_dispatcher.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from remote_agent.dispatcher import Dispatcher
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.config import AgentConfig


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.agent = AgentConfig(daily_budget_usd=50.0)
    return config


@pytest.fixture
def deps():
    return {
        "db": AsyncMock(),
        "github": AsyncMock(),
        "agent_service": AsyncMock(),
        "workspace_mgr": AsyncMock(),
    }


@pytest.fixture
def dispatcher(mock_config, deps):
    return Dispatcher(mock_config, deps["db"], deps["github"],
                      deps["agent_service"], deps["workspace_mgr"])


async def test_routes_new_issue_to_planning(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="new")
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    # Mock the planning handler
    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.return_value = PhaseResult(next_phase="plan_review")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    deps["db"].update_issue_phase.assert_called_once_with(1, "plan_review")
    deps["db"].mark_event_processed.assert_called_once_with(1)


async def test_budget_exceeded_leaves_event_unprocessed(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="new")
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 100.0  # Over budget

    await dispatcher.process_events()

    deps["db"].mark_event_processed.assert_not_called()
    deps["github"].post_comment.assert_called_once()  # Budget notification
    deps["db"].set_budget_notified.assert_called_once()


async def test_handler_error_transitions_to_error(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="new")
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.side_effect = Exception("Agent crashed")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    deps["db"].update_issue_phase.assert_called_with(1, "error")
    deps["db"].mark_event_processed.assert_called_once()


async def test_recover_interrupted_issues(dispatcher, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="planning")
    deps["db"].get_active_issues.return_value = [issue]
    deps["db"].get_unprocessed_events.return_value = []  # No events pending

    await dispatcher.recover_interrupted_issues()

    deps["db"].update_issue_phase.assert_called_once_with(1, "error")
    deps["db"].update_issue_error.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dispatcher.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement dispatcher**

```python
# src/remote_agent/dispatcher.py
from __future__ import annotations
import logging

from remote_agent.config import Config
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.agent import AgentService
from remote_agent.workspace import WorkspaceManager
from remote_agent.models import Issue, Event, PhaseResult
from remote_agent.phases.planning import PlanningHandler
from remote_agent.phases.plan_review import PlanReviewHandler
from remote_agent.phases.implementation import ImplementationHandler
from remote_agent.phases.code_review import CodeReviewHandler

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, config: Config, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager):
        self.config = config
        self.db = db
        self.github = github
        self._planning = PlanningHandler(db, github, agent_service, workspace_mgr)
        self._plan_review = PlanReviewHandler(db, github, agent_service)
        self._implementation = ImplementationHandler(db, github, agent_service, workspace_mgr)
        self._code_review = CodeReviewHandler(db, github, agent_service, workspace_mgr)

    async def process_events(self):
        events = await self.db.get_unprocessed_events()
        for event in events:
            await self._process_event(event)

    async def recover_interrupted_issues(self):
        active = await self.db.get_active_issues()
        events = await self.db.get_unprocessed_events()
        active_with_events = {e.issue_id for e in events}
        for issue in active:
            if issue.id not in active_with_events:
                logger.warning("Recovering interrupted issue #%d (was in %s)",
                              issue.issue_number, issue.phase)
                await self.db.update_issue_phase(issue.id, "error")
                await self.db.update_issue_error(issue.id, "Interrupted by restart")

    async def _process_event(self, event: Event):
        issue = await self.db.get_issue_by_id(event.issue_id)
        if not issue:
            await self.db.mark_event_processed(event.id)
            return

        handler = self._get_handler(issue, event)
        if not handler:
            await self.db.mark_event_processed(event.id)
            return

        target_phase = self._determine_target_phase(issue, event)
        if target_phase in ("planning", "implementing"):
            daily_spend = await self.db.get_daily_spend()
            if daily_spend >= self.config.agent.daily_budget_usd:
                if not issue.budget_notified:
                    target = issue.pr_number or issue.issue_number
                    try:
                        await self.github.post_comment(
                            issue.repo_owner, issue.repo_name, target,
                            "Daily budget limit reached. Will resume when budget resets.",
                        )
                    except Exception:
                        logger.exception("Failed to post budget notification")
                    await self.db.set_budget_notified(issue.id, True)
                return  # Leave event unprocessed

        # Reset plan_approved on reopen events (spec requirement)
        if event.event_type == "reopen":
            await self.db.set_plan_approved(issue.id, False)

        logger.info("Processing event %d: issue #%d (%s -> %s)",
                    event.id, issue.issue_number, issue.phase, target_phase)

        try:
            result = await handler.handle(issue, event)
            await self.db.update_issue_phase(issue.id, result.next_phase)
            if result.error_message:
                await self.db.update_issue_error(issue.id, result.error_message)
            # Reset budget notification on successful processing
            if issue.budget_notified:
                await self.db.set_budget_notified(issue.id, False)
        except Exception as e:
            logger.exception("Error processing event %d for issue #%d", event.id, issue.issue_number)
            await self.db.update_issue_phase(issue.id, "error")
            await self.db.update_issue_error(issue.id, str(e))
            target = issue.pr_number or issue.issue_number
            try:
                await self.github.post_comment(
                    issue.repo_owner, issue.repo_name, target,
                    f"Agent encountered an error:\n```\n{str(e)}\n```\nComment 'retry' to try again.",
                )
            except Exception:
                logger.exception("Failed to post error comment")
        finally:
            await self.db.mark_event_processed(event.id)

    def _get_handler(self, issue: Issue, event: Event):
        target = self._determine_target_phase(issue, event)
        if target == "planning":
            return self._planning
        elif target == "plan_review":
            return self._plan_review
        elif target == "implementing":
            return self._implementation
        elif target == "code_review":
            return self._code_review
        return None

    def _determine_target_phase(self, issue: Issue, event: Event) -> str | None:
        if event.event_type == "new_issue" and issue.phase == "new":
            return "planning"
        if event.event_type == "reopen":
            return "planning"
        if event.event_type == "revision_requested":
            # Target phase encoded in the context of who created the event
            if issue.phase in ("planning", "plan_review"):
                return "planning"
            if issue.phase in ("implementing", "code_review"):
                return "implementing" if issue.plan_approved else "planning"
            return "planning"
        if event.event_type == "new_comment":
            if issue.phase == "plan_review":
                return "plan_review"
            if issue.phase == "code_review":
                return "code_review"
            if issue.phase == "error":
                return "implementing" if issue.plan_approved else "planning"
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dispatcher.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: event dispatcher with budget gating and error recovery"
```

---

### Task 11: Main Loop

**Files:**
- Create: `src/remote_agent/main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write failing test for main module**

```python
# tests/test_main.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.main import create_app


async def test_create_app_initializes_components(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
repos:
  - owner: "o"
    name: "r"
users:
  - "u"
polling:
  interval_seconds: 60
trigger:
  label: "agent"
workspace:
  base_dir: "/tmp/ws"
database:
  path: "data/test.db"
agent:
  default_model: "sonnet"
  planning_model: "opus"
  implementation_model: "sonnet"
  review_model: "sonnet"
  orchestrator_model: "haiku"
  max_turns: 200
  max_budget_usd: 10.0
  daily_budget_usd: 50.0
""")
    app = await create_app(str(config_file))
    assert app.poller is not None
    assert app.dispatcher is not None
    await app.db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement main module**

```python
# src/remote_agent/main.py
from __future__ import annotations
import asyncio
import logging
import logging.handlers
from dataclasses import dataclass

from remote_agent.config import load_config, Config
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.workspace import WorkspaceManager
from remote_agent.agent import AgentService
from remote_agent.poller import Poller
from remote_agent.dispatcher import Dispatcher

logger = logging.getLogger("remote_agent")


@dataclass
class App:
    config: Config
    db: Database
    poller: Poller
    dispatcher: Dispatcher


async def create_app(config_path: str = "config.yaml") -> App:
    config = load_config(config_path)

    db = await Database.initialize(config.database.path)
    github = GitHubService()
    workspace_mgr = WorkspaceManager(config, github)
    agent_service = AgentService(config, db)
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr)

    return App(config=config, db=db, poller=poller, dispatcher=dispatcher)


async def run(config_path: str = "config.yaml"):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                "remote-agent.log", maxBytes=10_000_000, backupCount=3,
            ),
        ],
    )

    app = await create_app(config_path)

    logger.info("Remote agent started. Polling %d repos every %ds.",
                len(app.config.repos), app.config.polling.interval_seconds)

    await app.dispatcher.recover_interrupted_issues()

    try:
        while True:
            try:
                await app.poller.poll_once()
                await app.dispatcher.process_events()
            except Exception:
                logger.exception("Unexpected error in main loop")
            await asyncio.sleep(app.config.polling.interval_seconds)
    finally:
        await app.db.close()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/main.py tests/test_main.py
git commit -m "feat: main entry point with polling loop and startup recovery"
```

---

### Task 12: Integration Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test for happy path**

```python
# tests/test_integration.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.db import Database
from remote_agent.poller import Poller
from remote_agent.dispatcher import Dispatcher
from remote_agent.config import Config, RepoConfig, PollingConfig, TriggerConfig, WorkspaceConfig, DatabaseConfig, AgentConfig
from remote_agent.agent import AgentResult, CommentInterpretation


@pytest.fixture
def config():
    return Config(
        repos=[RepoConfig(owner="owner", name="repo")],
        users=["testuser"],
        polling=PollingConfig(interval_seconds=60),
        trigger=TriggerConfig(label="agent"),
        workspace=WorkspaceConfig(base_dir="/tmp/ws"),
        database=DatabaseConfig(path=""),  # Will be overridden
        agent=AgentConfig(),
    )


@pytest.fixture
async def db(tmp_path):
    database = await Database.initialize(str(tmp_path / "test.db"))
    yield database
    await database.close()


@pytest.fixture
def github():
    return AsyncMock()


@pytest.fixture
def agent_service():
    return AsyncMock()


@pytest.fixture
def workspace_mgr():
    mgr = AsyncMock()
    mgr.cleanup = MagicMock()
    return mgr


async def test_full_lifecycle_happy_path(config, db, github, agent_service, workspace_mgr):
    """Test: new issue -> planning -> plan_review -> approve -> implementing -> code_review -> approve -> completed"""
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr)
    # Override dispatcher handlers to use our mocks
    dispatcher._planning.agent_service = agent_service
    dispatcher._planning.workspace_mgr = workspace_mgr
    dispatcher._planning.github = github
    dispatcher._plan_review.agent_service = agent_service
    dispatcher._plan_review.github = github
    dispatcher._implementation.agent_service = agent_service
    dispatcher._implementation.workspace_mgr = workspace_mgr
    dispatcher._implementation.github = github
    dispatcher._code_review.agent_service = agent_service
    dispatcher._code_review.workspace_mgr = workspace_mgr
    dispatcher._code_review.github = github

    # Step 1: Poller detects new issue
    github.list_issues.return_value = [
        {"number": 1, "title": "Add feature", "body": "Details", "author": {"login": "testuser"}}
    ]
    await poller.poll_once()
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "new"

    # Step 2: Dispatcher routes to planning
    workspace_mgr.ensure_workspace.return_value = "/tmp/ws"
    workspace_mgr.get_head_commit.return_value = "abc123"
    agent_service.run_planning.return_value = AgentResult(
        success=True, session_id="s1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )
    github.create_pr.return_value = 5

    with patch("pathlib.Path.exists", return_value=False):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "plan_review"
    assert issue.pr_number == 5

    # Step 3: Human approves plan
    github.get_pr_comments.return_value = [
        {"id": 100, "body": "LGTM, implement it", "author": "testuser", "created_at": "2026-01-01"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "implementing"
    assert issue.plan_approved is True

    # Step 4: Implementation runs (triggered by revision_requested event from plan_review handler)
    agent_service.run_implementation.return_value = AgentResult(
        success=True, session_id="s2", cost_usd=3.0, input_tokens=500, output_tokens=1000,
    )

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="## Plan"):
        await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "code_review"

    # Step 5: Human approves code
    github.get_pr_comments.return_value = [
        {"id": 200, "body": "Ship it!", "author": "testuser", "created_at": "2026-01-02"}
    ]
    await poller.poll_once()

    agent_service.interpret_comment.return_value = CommentInterpretation(intent="approve")
    await dispatcher.process_events()

    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "completed"
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "feat: integration test for full issue lifecycle"
```

---

### Task 13: Run Full Test Suite and Final Verification

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Verify project installs cleanly**

Run: `pip install -e ".[dev]" && python -c "from remote_agent.main import main; print('OK')"`
Expected: "OK"

- [ ] **Step 3: Final commit with any fixes**

```bash
git add -A
git commit -m "chore: final cleanup and verification"
```
