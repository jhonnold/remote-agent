# Logging and Traceability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured JSON logging with correlation IDs and a dual-write audit system (SQLite + JSONL) to the remote-agent.

**Architecture:** New `logging_config.py` provides JSON formatter + ContextVar-based correlation. New `audit.py` provides dual-write audit logger. All existing modules gain targeted log statements. Dispatcher uses `contextvars.copy_context()` for per-event isolation.

**Tech Stack:** Python stdlib `logging` + `contextvars` + `json`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-26-logging-and-traceability-design.md`

---

### Task 1: LoggingConfig dataclass and config.py changes

**Files:**
- Modify: `src/remote_agent/config.py:30-55` (add dataclass), `src/remote_agent/config.py:85-93` (update load_config)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_logging.py`:

```python
# tests/test_config_logging.py
from __future__ import annotations
import pytest
from remote_agent.config import load_config, LoggingConfig

async def test_config_loads_logging_section(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
repos:
  - owner: o
    name: r
users: [u]
polling: {interval_seconds: 30}
trigger: {label: agent}
workspace: {base_dir: /tmp/ws}
database: {path: test.db}
agent: {}
logging:
  level: DEBUG
  file: custom.log
  audit_file: custom-audit.jsonl
""")
    config = load_config(str(config_file))
    assert config.logging.level == "DEBUG"
    assert config.logging.file == "custom.log"
    assert config.logging.audit_file == "custom-audit.jsonl"


async def test_config_defaults_when_logging_section_absent(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
repos:
  - owner: o
    name: r
users: [u]
polling: {}
trigger: {}
workspace: {}
database: {path: test.db}
agent: {}
""")
    config = load_config(str(config_file))
    assert config.logging.level == "INFO"
    assert config.logging.file == "remote-agent.log"
    assert config.logging.audit_file == "audit.jsonl"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_logging.py -v`
Expected: FAIL — `LoggingConfig` does not exist, `Config` has no `logging` attribute

- [ ] **Step 3: Write minimal implementation**

In `src/remote_agent/config.py`, add after `DatabaseConfig` (after line 33):

```python
@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "remote-agent.log"
    audit_file: str = "audit.jsonl"
```

In the `Config` dataclass (line 48-55), add the field:

```python
@dataclass
class Config:
    repos: list[RepoConfig]
    users: list[str]
    polling: PollingConfig
    trigger: TriggerConfig
    workspace: WorkspaceConfig
    database: DatabaseConfig
    agent: AgentConfig
    logging: LoggingConfig = field(default_factory=LoggingConfig)
```

In `load_config()`, update the return statement (currently line 85-93) to pass `logging`:

```python
    # Resolve logging file paths relative to config file
    logging_raw = raw.get("logging", {})
    log_file = logging_raw.get("file", "remote-agent.log")
    if not Path(log_file).is_absolute():
        logging_raw["file"] = str(path.parent / log_file)
    audit_file = logging_raw.get("audit_file", "audit.jsonl")
    if not Path(audit_file).is_absolute():
        logging_raw["audit_file"] = str(path.parent / audit_file)

    return Config(
        repos=repos,
        users=users,
        polling=PollingConfig(**raw.get("polling", {})),
        trigger=TriggerConfig(**raw.get("trigger", {})),
        workspace=WorkspaceConfig(**raw.get("workspace", {})),
        database=DatabaseConfig(path=db_path),
        agent=AgentConfig(**raw.get("agent", {})),
        logging=LoggingConfig(**logging_raw),
    )
```

Also add `LoggingConfig` to the imports in `tests/test_integration.py` config fixture — update the `Config(...)` call at line 13-21 to include `logging=LoggingConfig()`. Import `LoggingConfig` from `remote_agent.config`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_logging.py tests/test_integration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/config.py tests/test_config_logging.py tests/test_integration.py
git commit -m "feat: add LoggingConfig dataclass with level, file, audit_file"
```

---

### Task 2: JsonFormatter and CorrelationFilter

**Files:**
- Create: `src/remote_agent/logging_config.py`
- Create: `tests/test_logging_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_logging_config.py`:

```python
# tests/test_logging_config.py
from __future__ import annotations
import json
import logging
import os
import pytest
from contextvars import Token

from remote_agent.logging_config import (
    JsonFormatter,
    CorrelationFilter,
    current_issue_id,
    current_event_id,
    current_operation_id,
)


@pytest.fixture(autouse=True)
def reset_context_vars():
    """Reset all ContextVars after each test to prevent cross-test contamination."""
    yield
    current_issue_id.set(None)
    current_event_id.set(None)
    current_operation_id.set(None)


def _make_record(msg="test message", level=logging.INFO, name="test.logger"):
    return logging.LogRecord(
        name=name, level=level, pathname="", lineno=0,
        msg=msg, args=(), exc_info=None,
    )


class TestJsonFormatter:
    def test_produces_valid_json(self):
        fmt = JsonFormatter()
        record = _make_record()
        output = fmt.format(record)
        data = json.loads(output)
        assert data["message"] == "test message"
        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert "time" in data

    def test_omits_none_fields(self):
        fmt = JsonFormatter()
        record = _make_record()
        output = fmt.format(record)
        data = json.loads(output)
        assert "issue_id" not in data
        assert "event_id" not in data
        assert "operation_id" not in data

    def test_includes_exc_info(self):
        fmt = JsonFormatter()
        record = _make_record()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record.exc_info = sys.exc_info()
        output = fmt.format(record)
        data = json.loads(output)
        assert "exc_info" in data
        assert "ValueError" in data["exc_info"]


class TestCorrelationFilter:
    def test_injects_context_vars_when_set(self):
        current_issue_id.set(42)
        current_event_id.set(7)
        filt = CorrelationFilter()
        record = _make_record()
        filt.filter(record)
        assert record.issue_id == 42
        assert record.event_id == 7

    def test_sets_none_when_context_vars_unset(self):
        filt = CorrelationFilter()
        record = _make_record()
        filt.filter(record)
        assert record.issue_id is None
        assert record.event_id is None
        assert record.operation_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_logging_config.py -v`
Expected: FAIL — `remote_agent.logging_config` does not exist

- [ ] **Step 3: Write minimal implementation**

Create `src/remote_agent/logging_config.py`:

```python
# src/remote_agent/logging_config.py
from __future__ import annotations
import json
import logging
from contextvars import ContextVar

current_issue_id: ContextVar[int | None] = ContextVar("current_issue_id", default=None)
current_event_id: ContextVar[int | None] = ContextVar("current_event_id", default=None)
current_operation_id: ContextVar[str | None] = ContextVar("current_operation_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d: dict = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "issue_id": getattr(record, "issue_id", None),
            "event_id": getattr(record, "event_id", None),
            "operation_id": getattr(record, "operation_id", None),
        }
        if record.exc_info:
            d["exc_info"] = self.formatException(record.exc_info)
        return json.dumps({k: v for k, v in d.items() if v is not None})


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.issue_id = current_issue_id.get(None)
        record.event_id = current_event_id.get(None)
        record.operation_id = current_operation_id.get(None)
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_logging_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/logging_config.py tests/test_logging_config.py
git commit -m "feat: add JsonFormatter and CorrelationFilter with ContextVars"
```

---

### Task 3: setup_logging function

**Files:**
- Modify: `src/remote_agent/logging_config.py` (add `setup_logging`)
- Modify: `tests/test_logging_config.py` (add tests)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_logging_config.py`:

```python
from remote_agent.logging_config import setup_logging
from remote_agent.config import LoggingConfig, Config, RepoConfig, PollingConfig, TriggerConfig, WorkspaceConfig, DatabaseConfig, AgentConfig


def _make_config(tmp_path, level="INFO"):
    return Config(
        repos=[RepoConfig(owner="o", name="r")],
        users=["u"],
        polling=PollingConfig(),
        trigger=TriggerConfig(),
        workspace=WorkspaceConfig(),
        database=DatabaseConfig(),
        agent=AgentConfig(),
        logging=LoggingConfig(level=level, file=str(tmp_path / "test.log"), audit_file=str(tmp_path / "audit.jsonl")),
    )


class TestSetupLogging:
    def test_emits_json_to_file(self, tmp_path):
        config = _make_config(tmp_path)
        setup_logging(config)
        test_logger = logging.getLogger("test.setup")
        test_logger.info("hello from test")
        log_file = tmp_path / "test.log"
        assert log_file.exists()
        line = log_file.read_text().strip().split("\n")[-1]
        data = json.loads(line)
        assert data["message"] == "hello from test"
        # Cleanup: remove handlers to avoid interference with other tests
        logging.root.handlers.clear()

    def test_loglevel_env_overrides_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOGLEVEL", "DEBUG")
        config = _make_config(tmp_path, level="WARNING")
        setup_logging(config)
        assert logging.root.level == logging.DEBUG
        logging.root.handlers.clear()

    def test_config_level_used_when_no_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LOGLEVEL", raising=False)
        config = _make_config(tmp_path, level="WARNING")
        setup_logging(config)
        assert logging.root.level == logging.WARNING
        logging.root.handlers.clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_logging_config.py::TestSetupLogging -v`
Expected: FAIL — `setup_logging` not yet defined

- [ ] **Step 3: Write minimal implementation**

Add to `src/remote_agent/logging_config.py`:

```python
import logging.handlers
import os

from remote_agent.config import Config


def setup_logging(config: Config) -> None:
    """Configure structured JSON logging. Replaces any existing root handlers."""
    level_name = os.environ.get("LOGLEVEL", config.logging.level).upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    formatter = JsonFormatter()
    correlation = CorrelationFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(correlation)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        config.logging.file, maxBytes=10_000_000, backupCount=3,
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(correlation)
    root.addHandler(file_handler)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_logging_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/remote_agent/logging_config.py tests/test_logging_config.py
git commit -m "feat: add setup_logging with JSON handlers and env var override"
```

---

### Task 4: Wire setup_logging into main.py

**Files:**
- Modify: `src/remote_agent/main.py:2-5` (imports), `src/remote_agent/main.py:40-50` (replace basicConfig)

- [ ] **Step 1: Modify main.py**

Replace the `logging.basicConfig(...)` block (lines 41-50) in `run()` with the two-phase setup:

```python
async def run(config_path: str = "config.yaml"):
    # Phase 1: minimal console logging until config is available
    logging.basicConfig(level=logging.INFO)

    app = await create_app(config_path)

    # Phase 2: reconfigure with structured JSON logging
    from remote_agent.logging_config import setup_logging
    setup_logging(app.config)

    logger.info("Remote agent started. Polling %d repos every %ds.",
                len(app.config.repos), app.config.polling.interval_seconds)
```

- [ ] **Step 2: Run full test suite to verify nothing breaks**

Run: `pytest -v`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/remote_agent/main.py
git commit -m "feat: wire setup_logging into main.py with two-phase bootstrap"
```

---

### Task 5: AuditLogger and audit_log table

**Files:**
- Create: `src/remote_agent/audit.py`
- Modify: `src/remote_agent/db.py:10-54` (add table to SCHEMA)
- Create: `tests/test_audit.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit.py`:

```python
# tests/test_audit.py
from __future__ import annotations
import json
import pytest
from unittest.mock import patch

from remote_agent.audit import AuditLogger
from remote_agent.db import Database
from remote_agent.logging_config import current_issue_id, current_event_id


@pytest.fixture(autouse=True)
def reset_context_vars():
    yield
    current_issue_id.set(None)
    current_event_id.set(None)


@pytest.fixture
async def db(tmp_path):
    database = await Database.initialize(str(tmp_path / "test.db"))
    yield database
    await database.close()


@pytest.fixture
def audit_file(tmp_path):
    return tmp_path / "audit.jsonl"


@pytest.fixture
def audit(db, audit_file):
    a = AuditLogger(db, str(audit_file))
    yield a
    a.close()


async def test_log_writes_to_file_and_db(audit, db, audit_file):
    await audit.log("phase_transition", "plan_created", issue_id=1, success=True)

    # Check JSONL file
    lines = audit_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["category"] == "phase_transition"
    assert data["action"] == "plan_created"
    assert data["issue_id"] == 1
    assert data["success"] is True

    # Check DB
    cursor = await db._conn.execute("SELECT * FROM audit_log")
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["category"] == "phase_transition"
    assert rows[0]["success"] == 1


async def test_log_uses_context_vars_as_defaults(audit, audit_file):
    current_issue_id.set(42)
    current_event_id.set(7)
    await audit.log("git_op", "branch_push", success=True)

    data = json.loads(audit_file.read_text().strip())
    assert data["issue_id"] == 42
    assert data["event_id"] == 7


async def test_log_explicit_args_override_context_vars(audit, audit_file):
    current_issue_id.set(42)
    await audit.log("git_op", "branch_push", issue_id=99, success=True)

    data = json.loads(audit_file.read_text().strip())
    assert data["issue_id"] == 99


async def test_log_with_duration(audit, db, audit_file):
    await audit.log("github_api", "create_pr", issue_id=1, duration_ms=1500, success=True)

    data = json.loads(audit_file.read_text().strip())
    assert data["duration_ms"] == 1500

    cursor = await db._conn.execute("SELECT duration_ms FROM audit_log")
    row = await cursor.fetchone()
    assert row["duration_ms"] == 1500


async def test_log_without_duration_stores_null(audit, db):
    await audit.log("phase_transition", "started", issue_id=1, success=True)

    cursor = await db._conn.execute("SELECT duration_ms FROM audit_log")
    row = await cursor.fetchone()
    assert row["duration_ms"] is None


async def test_log_with_error(audit, audit_file):
    await audit.log("phase_transition", "handler_failed", issue_id=1,
                     success=False, error_message="Agent crashed")

    data = json.loads(audit_file.read_text().strip())
    assert data["success"] is False
    assert data["error_message"] == "Agent crashed"


async def test_close_flushes_file(db, tmp_path):
    audit_file = tmp_path / "audit2.jsonl"
    audit = AuditLogger(db, str(audit_file))
    await audit.log("phase_transition", "test", issue_id=1, success=True)
    audit.close()
    assert audit_file.read_text().strip() != ""


async def test_file_write_failure_prevents_db_write(db, tmp_path):
    audit_file = tmp_path / "audit3.jsonl"
    audit = AuditLogger(db, str(audit_file))

    with patch.object(audit._file, "write", side_effect=IOError("disk full")):
        with pytest.raises(IOError):
            await audit.log("phase_transition", "test", issue_id=1, success=True)

    # DB should NOT have the record
    cursor = await db._conn.execute("SELECT COUNT(*) as cnt FROM audit_log")
    row = await cursor.fetchone()
    assert row["cnt"] == 0
    audit.close()


async def test_context_vars_none_when_unset(audit, audit_file):
    await audit.log("phase_transition", "test", success=True)
    data = json.loads(audit_file.read_text().strip())
    assert data.get("issue_id") is None
    assert data.get("event_id") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audit.py -v`
Expected: FAIL — `remote_agent.audit` does not exist, `audit_log` table not in schema

- [ ] **Step 3: Add audit_log table to db.py SCHEMA**

In `src/remote_agent/db.py`, append to the `SCHEMA` string (after line 53, before the closing `"""`):

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
);
CREATE INDEX IF NOT EXISTS idx_audit_log_issue_id ON audit_log(issue_id);
```

- [ ] **Step 4: Create audit.py**

Create `src/remote_agent/audit.py`:

```python
# src/remote_agent/audit.py
from __future__ import annotations
import json

from remote_agent.db import Database
from remote_agent.logging_config import current_issue_id, current_event_id


class AuditLogger:
    def __init__(self, db: Database, audit_file_path: str):
        self._db = db
        self._file = open(audit_file_path, "a")

    def close(self) -> None:
        self._file.flush()
        self._file.close()

    async def log(
        self,
        category: str,
        action: str,
        *,
        issue_id: int | None = None,
        event_id: int | None = None,
        detail: dict | None = None,
        duration_ms: int | None = None,
        success: bool,
        error_message: str | None = None,
    ) -> None:
        # Use ContextVar defaults when not passed explicitly
        if issue_id is None:
            issue_id = current_issue_id.get(None)
        if event_id is None:
            event_id = current_event_id.get(None)

        record = {
            "category": category,
            "action": action,
            "issue_id": issue_id,
            "event_id": event_id,
            "detail": detail,
            "duration_ms": duration_ms,
            "success": success,
            "error_message": error_message,
        }

        # File first — if this fails, DB write is skipped
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

        # Then DB
        detail_json = json.dumps(detail) if detail else None
        await self._db._conn.execute(
            """INSERT INTO audit_log
               (issue_id, event_id, category, action, detail, duration_ms, success, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (issue_id, event_id, category, action, detail_json, duration_ms,
             int(success), error_message),
        )
        await self._db._conn.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_audit.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS (new schema additions use `CREATE TABLE IF NOT EXISTS`)

- [ ] **Step 7: Commit**

```bash
git add src/remote_agent/audit.py src/remote_agent/db.py tests/test_audit.py
git commit -m "feat: add AuditLogger with dual-write to JSONL and SQLite"
```

---

### Task 6: Wire AuditLogger into Dispatcher and main.py

**Files:**
- Modify: `src/remote_agent/dispatcher.py:19-28` (constructor), `src/remote_agent/dispatcher.py:30-33` (context isolation), `src/remote_agent/dispatcher.py:88-99` (error path audit)
- Modify: `src/remote_agent/main.py:27-37` (create_app), `src/remote_agent/main.py:67-68` (finally block)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dispatcher.py`:

```python
from remote_agent.logging_config import current_issue_id


async def test_context_vars_isolated_per_event(mock_config, deps):
    """Verify ContextVar isolation: each event gets its own issue_id context."""
    captured = {}

    async def capturing_handle(issue, event):
        captured[event.id] = current_issue_id.get(None)
        return PhaseResult(next_phase="plan_review")

    issue1 = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                   title="T", body="", phase="new")
    issue2 = Issue(id=2, repo_owner="o", repo_name="r", issue_number=2,
                   title="T2", body="", phase="new")
    event1 = Event(id=10, issue_id=1, event_type="new_issue", payload={})
    event2 = Event(id=20, issue_id=2, event_type="new_issue", payload={})

    deps["db"].get_unprocessed_events.return_value = [event1, event2]
    deps["db"].get_issue_by_id.side_effect = lambda id: {1: issue1, 2: issue2}[id]
    deps["db"].get_daily_spend.return_value = 0.0

    dispatcher = Dispatcher(mock_config, deps["db"], deps["github"],
                            deps["agent_service"], deps["workspace_mgr"])

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.side_effect = capturing_handle
        mock_handler.return_value = handler
        await dispatcher.process_events()

    assert captured[10] == 1
    assert captured[20] == 2


async def test_error_path_calls_audit(mock_config, deps):
    audit = AsyncMock()
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=1,
                  title="T", body="", phase="new")
    event = Event(id=1, issue_id=1, event_type="new_issue", payload={})
    deps["db"].get_unprocessed_events.return_value = [event]
    deps["db"].get_issue_by_id.return_value = issue
    deps["db"].get_daily_spend.return_value = 0.0

    dispatcher = Dispatcher(mock_config, deps["db"], deps["github"],
                            deps["agent_service"], deps["workspace_mgr"], audit=audit)

    with patch.object(dispatcher, "_get_handler") as mock_handler:
        handler = AsyncMock()
        handler.handle.side_effect = Exception("crash")
        mock_handler.return_value = handler
        await dispatcher.process_events()

    audit.log.assert_called_once()
    call_kwargs = audit.log.call_args
    assert call_kwargs.kwargs.get("success") is False or (len(call_kwargs.args) > 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dispatcher.py::test_context_vars_isolated_per_event tests/test_dispatcher.py::test_error_path_calls_audit -v`
Expected: FAIL — Dispatcher doesn't use ContextVars or accept `audit`

- [ ] **Step 3: Implement Dispatcher changes**

Update `src/remote_agent/dispatcher.py`:

Add imports at the top:
```python
import asyncio
import contextvars
from remote_agent.logging_config import current_issue_id, current_event_id
```

Update `__init__` (lines 20-28):
```python
class Dispatcher:
    def __init__(self, config: Config, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        self.config = config
        self.db = db
        self.github = github
        self.audit = audit
        self._planning = PlanningHandler(db, github, agent_service, workspace_mgr)
        self._plan_review = PlanReviewHandler(db, github, agent_service)
        self._implementation = ImplementationHandler(db, github, agent_service, workspace_mgr)
        self._code_review = CodeReviewHandler(db, github, agent_service, workspace_mgr)
```

Update `process_events` (lines 30-33):
```python
    async def process_events(self):
        events = await self.db.get_unprocessed_events()
        logger.debug("Fetched %d events to process", len(events))
        for event in events:
            ctx = contextvars.copy_context()
            ctx.run(current_issue_id.set, event.issue_id)
            ctx.run(current_event_id.set, event.id)
            await asyncio.create_task(
                self._process_event(event), context=ctx
            )
```

Update `recover_interrupted_issues` (after line 44, add summary):
```python
    async def recover_interrupted_issues(self):
        active = await self.db.get_active_issues()
        events = await self.db.get_unprocessed_events()
        active_with_events = {e.issue_id for e in events}
        recovered = 0
        for issue in active:
            if issue.id not in active_with_events:
                logger.warning("Recovering interrupted issue #%d (was in %s)",
                              issue.issue_number, issue.phase)
                await self.db.update_issue_phase(issue.id, "error")
                await self.db.update_issue_error(issue.id, "Interrupted by restart")
                recovered += 1
        if recovered:
            logger.info("Recovered %d interrupted issues on startup", recovered)
```

Add audit call in the error path (inside the `except Exception as e` block, after line 91):
```python
        except Exception as e:
            logger.exception("Error processing event %d for issue #%d", event.id, issue.issue_number)
            await self.db.update_issue_phase(issue.id, "error")
            await self.db.update_issue_error(issue.id, str(e))
            if self.audit:
                await self.audit.log(
                    "phase_transition", "error",
                    issue_id=issue.id, event_id=event.id,
                    success=False, error_message=str(e),
                )
            target = issue.pr_number or issue.issue_number
            ...
```

- [ ] **Step 4: Wire AuditLogger in main.py**

Update `src/remote_agent/main.py`:

Add import:
```python
from remote_agent.audit import AuditLogger
```

Update `create_app` to create and pass AuditLogger:
```python
async def create_app(config_path: str = "config.yaml") -> App:
    config = load_config(config_path)

    db = await Database.initialize(config.database.path)
    audit = AuditLogger(db, config.logging.audit_file)
    github = GitHubService()
    workspace_mgr = WorkspaceManager(config, github)
    agent_service = AgentService(config, db)
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)

    return App(config=config, db=db, poller=poller, dispatcher=dispatcher, audit=audit)
```

Add `audit` to `App` dataclass:
```python
@dataclass
class App:
    config: Config
    db: Database
    poller: Poller
    dispatcher: Dispatcher
    audit: AuditLogger | None = None
```

Update the `finally` block in `run()`:
```python
    finally:
        if app.audit:
            app.audit.close()
        await app.db.close()
```

- [ ] **Step 5: Run tests**

Run: `pytest -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/remote_agent/dispatcher.py src/remote_agent/main.py tests/test_dispatcher.py
git commit -m "feat: wire AuditLogger into Dispatcher with ContextVar isolation"
```

---

### Task 7: Add logging to github.py and workspace.py

**Files:**
- Modify: `src/remote_agent/github.py:1-23` (add logger, DEBUG in _run_gh)
- Modify: `src/remote_agent/workspace.py:1-72` (add logger, INFO/DEBUG throughout)

- [ ] **Step 1: Add logging to github.py**

In `src/remote_agent/github.py`, add import and logger after line 4:

```python
import logging

logger = logging.getLogger(__name__)
```

In `_run_gh` (line 13), add DEBUG log before the subprocess call with arg masking:

```python
    async def _run_gh(self, args: list[str], cwd: str | None = None) -> str:
        masked = []
        skip_next = False
        for i, arg in enumerate(args):
            if skip_next:
                masked.append(f"<{len(arg)} chars>")
                skip_next = False
            elif arg in ("--body", "--title"):
                masked.append(arg)
                skip_next = True
            else:
                masked.append(arg)
        logger.debug("gh %s", " ".join(masked))
        proc = await asyncio.create_subprocess_exec(
            ...
```

- [ ] **Step 2: Add logging to workspace.py**

In `src/remote_agent/workspace.py`, add import and logger after line 5:

```python
import logging

logger = logging.getLogger(__name__)
```

In `_run_git` (line 62), add DEBUG:
```python
    async def _run_git(self, args: list[str], cwd: str) -> str:
        logger.debug("git %s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            ...
```

In `ensure_workspace` (line 20), add INFO for both paths:
```python
    async def ensure_workspace(self, owner: str, repo: str, issue_number: int) -> str:
        path = self._workspace_path(owner, repo, issue_number)
        if not path.exists():
            logger.info("Cloning %s/%s into %s", owner, repo, path)
            path.parent.mkdir(parents=True, exist_ok=True)
            ...
        else:
            logger.info("Updating workspace for %s/%s", owner, repo)
            ...
```

In `ensure_branch` (line 35), add INFO for new branch:
```python
    async def ensure_branch(self, workspace: str, branch: str) -> None:
        try:
            await self._run_git(["checkout", branch], cwd=workspace)
            await self._run_git(["pull", "origin", branch], cwd=workspace)
        except GitError:
            logger.info("Created branch %s", branch)
            await self._run_git(["checkout", "-b", branch], cwd=workspace)
```

In `commit_and_push` (line 42), add INFO:
```python
    async def commit_and_push(self, workspace: str, branch: str, message: str) -> None:
        ...
        await self._run_git(["push", "-u", "origin", branch], cwd=workspace)
        logger.info("Pushed to branch %s", branch)
```

In `cleanup` (line 57), add DEBUG:
```python
    def cleanup(self, owner: str, repo: str, issue_number: int) -> None:
        path = self._workspace_path(owner, repo, issue_number)
        if path.exists():
            logger.debug("Cleaned up workspace %s", path)
            shutil.rmtree(path)
```

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/remote_agent/github.py src/remote_agent/workspace.py
git commit -m "feat: add structured logging to github.py and workspace.py"
```

---

### Task 8: Add logging to agent.py and db.py

**Files:**
- Modify: `src/remote_agent/agent.py:116-160` (add INFO/WARNING in _run_query)
- Modify: `src/remote_agent/db.py` (add DEBUG to all write methods)

- [ ] **Step 1: Add logging to agent.py _run_query**

In `_run_query` (line 116), add INFO at start before `run_id` creation:

```python
    async def _run_query(self, prompt: str, options, issue_id: int, phase: str,
                          allow_resume: bool = False) -> AgentResult:
        from claude_agent_sdk import query, ResultMessage

        logger.info("Starting %s query for issue %d", phase, issue_id)
        run_id = await self.db.create_agent_run(issue_id, phase)
        ...
```

After the `async for` loop completes successfully (before `await self.db.complete_agent_run`), add:
```python
            logger.info("Completed %s query for issue %d, cost=$%.4f, tokens=%d+%d, session=%s",
                        phase, issue_id, cost, input_tokens, output_tokens, session_id)
```

In the `except` block (line 154), add WARNING:
```python
        except Exception as e:
            logger.warning("Query failed for issue %d phase=%s: %s", issue_id, phase, e)
            await self.db.complete_agent_run(
                ...
```

- [ ] **Step 2: Add DEBUG logging to db.py write methods**

In `src/remote_agent/db.py`, add import and logger after line 4:
```python
import logging

logger = logging.getLogger(__name__)
```

Add `logger.debug(...)` as the first line inside each write method:

```python
    async def create_issue(self, ...):
        logger.debug("Creating issue %s/%s#%d", repo_owner, repo_name, issue_data["number"])
        ...

    async def update_issue_phase(self, issue_id: int, phase: str):
        logger.debug("Updated issue %d phase=%s", issue_id, phase)
        ...

    async def update_issue_branch(self, issue_id: int, branch: str):
        logger.debug("Updated issue %d branch=%s", issue_id, branch)
        ...

    async def update_issue_pr(self, issue_id: int, pr_number: int):
        logger.debug("Updated issue %d pr=%d", issue_id, pr_number)
        ...

    async def update_issue_workspace(self, issue_id: int, workspace_path: str):
        logger.debug("Updated issue %d workspace=%s", issue_id, workspace_path)
        ...

    async def set_plan_approved(self, issue_id: int, approved: bool):
        logger.debug("Set issue %d plan_approved=%s", issue_id, approved)
        ...

    async def set_plan_commit_hash(self, issue_id: int, commit_hash: str):
        logger.debug("Set issue %d plan_commit_hash=%s", issue_id, commit_hash)
        ...

    async def update_issue_error(self, issue_id: int, error_message: str):
        logger.debug("Updated issue %d error=%s", issue_id, error_message)
        ...

    async def set_budget_notified(self, issue_id: int, notified: bool):
        logger.debug("Set issue %d budget_notified=%s", issue_id, notified)
        ...

    async def update_last_comment_id(self, issue_id: int, comment_id: int):
        logger.debug("Updated issue %d last_comment_id=%d", issue_id, comment_id)
        ...

    async def create_event(self, issue_id: int, event_type: str, ...):
        logger.debug("Created event type=%s for issue %d", event_type, issue_id)
        ...

    async def create_comment_events(self, issue_id: int, comments: list[dict]):
        logger.debug("Creating %d comment events for issue %d", len(comments), issue_id)
        ...

    async def mark_event_processed(self, event_id: int):
        logger.debug("Marked event %d processed", event_id)
        ...

    async def create_agent_run(self, issue_id: int, phase: str) -> int:
        logger.debug("Created agent_run for issue %d phase=%s", issue_id, phase)
        ...

    async def complete_agent_run(self, run_id: int, ...):
        logger.debug("Completed agent_run %d result=%s", run_id, result)
        ...
```

Place each `logger.debug(...)` call after the DB operation succeeds (after the `await self._conn.commit()`), not before. For `create_issue`, place it after `await self._conn.commit()` but before `return cursor.lastrowid`. For `create_comment_events`, place it at the top of the method (before the transaction).

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/remote_agent/agent.py src/remote_agent/db.py
git commit -m "feat: add logging to agent.py (INFO) and db.py (DEBUG writes)"
```

---

### Task 9: Add logging and audit to phase handlers

**Files:**
- Modify: `src/remote_agent/phases/planning.py` (add audit param, INFO entry/exit, audit calls)
- Modify: `src/remote_agent/phases/implementation.py` (same)
- Modify: `src/remote_agent/phases/plan_review.py` (add audit param, audit calls)
- Modify: `src/remote_agent/phases/code_review.py` (add audit param, audit calls)
- Modify: `src/remote_agent/dispatcher.py:25-28` (pass audit to handlers)

- [ ] **Step 1: Update PlanningHandler**

In `src/remote_agent/phases/planning.py`, update constructor:
```python
class PlanningHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        self.db = db
        self.github = github
        self.agent_service = agent_service
        self.workspace_mgr = workspace_mgr
        self.audit = audit
```

Add INFO at entry and exit of `handle`, and audit call for PR creation:
```python
    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        logger.info("Handling planning for issue %d", issue.id)
        ...
        # After PR creation (after line 67):
        if self.audit:
            await self.audit.log(
                "github_api", "create_pr", issue_id=issue.id,
                detail={"pr_number": pr_number}, success=True,
            )
        ...
        logger.info("Completed planning for issue %d", issue.id)
        return PhaseResult(next_phase="plan_review")
```

Also add audit for the phase transition:
```python
        if self.audit:
            await self.audit.log("phase_transition", "plan_review",
                                  issue_id=issue.id, success=True)
```

- [ ] **Step 2: Update ImplementationHandler**

Same pattern — add `audit=None` to constructor, INFO entry/exit, audit for phase transition:
```python
class ImplementationHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        ...
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        logger.info("Handling implementation for issue %d", issue.id)
        ...
        logger.info("Completed implementation for issue %d", issue.id)
        if self.audit:
            await self.audit.log("phase_transition", "code_review",
                                  issue_id=issue.id, success=True)
        return PhaseResult(next_phase="code_review")
```

- [ ] **Step 3: Update PlanReviewHandler**

Add `audit=None` to constructor. Add audit for comment classification:
```python
class PlanReviewHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, audit=None):
        ...
        self.audit = audit

    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        ...
        logger.info("Plan review comment interpreted as: %s", interpretation.intent)
        if self.audit:
            await self.audit.log(
                "comment_classification", interpretation.intent,
                issue_id=issue.id, success=True,
            )
        ...
```

- [ ] **Step 4: Update CodeReviewHandler**

Same pattern as PlanReviewHandler:
```python
class CodeReviewHandler:
    def __init__(self, db: Database, github: GitHubService,
                 agent_service: AgentService, workspace_mgr: WorkspaceManager,
                 audit=None):
        ...
        self.audit = audit
```

Add audit after interpretation log.

- [ ] **Step 5: Update Dispatcher to pass audit to handlers**

In `src/remote_agent/dispatcher.py`, update handler construction (lines 25-28):
```python
        self._planning = PlanningHandler(db, github, agent_service, workspace_mgr, audit=audit)
        self._plan_review = PlanReviewHandler(db, github, agent_service, audit=audit)
        self._implementation = ImplementationHandler(db, github, agent_service, workspace_mgr, audit=audit)
        self._code_review = CodeReviewHandler(db, github, agent_service, workspace_mgr, audit=audit)
```

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS (audit param is optional, existing tests don't pass it)

- [ ] **Step 7: Commit**

```bash
git add src/remote_agent/phases/ src/remote_agent/dispatcher.py
git commit -m "feat: add logging and audit calls to all phase handlers"
```

---

### Task 10: Phase handler audit tests

**Files:**
- Modify: `tests/test_phases/test_planning.py` (add audit test)
- Modify: `tests/test_phases/test_plan_review.py` (add audit test)
- Modify: `tests/test_phases/test_implementation.py` (add audit test)
- Modify: `tests/test_phases/test_code_review.py` (add audit test)

- [ ] **Step 1: Add audit test to test_planning.py**

```python
async def test_planning_audit_records(deps, new_issue, new_issue_event):
    audit = AsyncMock()
    handler = PlanningHandler(deps["db"], deps["github"], deps["agent_service"],
                               deps["workspace_mgr"], audit=audit)

    deps["workspace_mgr"].ensure_workspace.return_value = "/tmp/ws"
    deps["workspace_mgr"].get_head_commit.return_value = "abc123"
    deps["agent_service"].run_planning.return_value = AgentResult(
        success=True, session_id="sess-1", cost_usd=1.0, input_tokens=100, output_tokens=200,
    )
    deps["github"].create_pr.return_value = 10

    result = await handler.handle(new_issue, new_issue_event)

    assert result.next_phase == "plan_review"
    # Verify audit was called for PR creation and phase transition
    assert audit.log.call_count >= 1
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "phase_transition" in categories
```

- [ ] **Step 2: Add similar tests for the other three handlers**

Each test creates a handler with `audit=AsyncMock()`, runs the handler, and asserts `audit.log` was called with expected categories. Follow the existing test patterns in each file for mock setup.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_phases/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_phases/
git commit -m "test: add audit assertion tests for all phase handlers"
```

---

### Task 11: Integration test audit assertions

**Files:**
- Modify: `tests/test_integration.py` (wire AuditLogger, assert audit records)

- [ ] **Step 1: Update integration test**

In `tests/test_integration.py`, add imports:
```python
import json
from remote_agent.audit import AuditLogger
from remote_agent.config import LoggingConfig
```

Update the `config` fixture to include `LoggingConfig`:
```python
@pytest.fixture
def config():
    return Config(
        ...
        logging=LoggingConfig(),
    )
```

Add an `audit` fixture:
```python
@pytest.fixture
async def audit(db, tmp_path):
    a = AuditLogger(db, str(tmp_path / "audit.jsonl"))
    yield a
    a.close()

@pytest.fixture
def audit_file(tmp_path):
    return tmp_path / "audit.jsonl"
```

Update `test_full_lifecycle_happy_path` to accept `audit` and `audit_file` fixtures, pass audit to Dispatcher:
```python
async def test_full_lifecycle_happy_path(config, db, github, agent_service, workspace_mgr, audit, audit_file):
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)
    ...
```

At the end of the test, add audit assertions:
```python
    # Verify audit trail
    audit_lines = audit_file.read_text().strip().split("\n")
    audit_records = [json.loads(line) for line in audit_lines]
    categories_and_actions = [(r["category"], r["action"]) for r in audit_records]

    # Should have phase transition records for the full lifecycle
    assert ("phase_transition", "plan_review") in categories_and_actions
    assert ("phase_transition", "code_review") in categories_and_actions

    # All records should be successful
    assert all(r["success"] for r in audit_records)
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add audit trail assertions to integration test"
```

---

### Task 12: Final verification

- [ ] **Step 1: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS

- [ ] **Step 2: Verify JSON log output manually**

Run: `LOGLEVEL=DEBUG python3 -c "
import asyncio, logging
from remote_agent.logging_config import setup_logging, current_issue_id
from remote_agent.config import Config, RepoConfig, PollingConfig, TriggerConfig, WorkspaceConfig, DatabaseConfig, AgentConfig, LoggingConfig
config = Config(repos=[RepoConfig('o','r')], users=['u'], polling=PollingConfig(), trigger=TriggerConfig(), workspace=WorkspaceConfig(), database=DatabaseConfig(), agent=AgentConfig(), logging=LoggingConfig(file='/tmp/test-log.json', audit_file='/tmp/test-audit.jsonl'))
setup_logging(config)
logger = logging.getLogger('test')
current_issue_id.set(42)
logger.info('Test message with correlation')
logger.debug('Debug message')
"`

Expected: Two JSON lines to stdout, each with `issue_id: 42`. Also written to `/tmp/test-log.json`.

- [ ] **Step 3: Commit any remaining fixes**

If all passes, no additional commit needed.
