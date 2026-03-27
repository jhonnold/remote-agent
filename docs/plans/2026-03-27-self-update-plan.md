# Self-Updating Agent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add opt-in auto-update so the agent detects new commits on `main`, pulls them, and restarts via exit code 42.

**Architecture:** New `AutoUpdater` class in `updater.py` with `check_for_update()` and `pull_update()` methods using `asyncio.create_subprocess_exec`. Config adds `AutoUpdateConfig` dataclass. Main loop calls updater after dispatch, before sleep. Exit code 42 + systemd `RestartForceExitStatus=42` for restart.

**Tech Stack:** Python 3.11+, asyncio, git CLI, pip CLI, pytest

---

### Task 1: Add `AutoUpdateConfig` to config

**Files:**
- Modify: `src/remote_agent/config.py:54-63` (add dataclass + field to `Config`)
- Modify: `src/remote_agent/config.py:66-111` (parse in `load_config`)
- Test: `tests/test_config.py`

**Step 1: Write failing tests**

Add to `tests/test_config.py`:

```python
def test_load_config_auto_update_enabled(tmp_path):
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
auto_update:
  enabled: true
""")
    config = load_config(str(config_file))
    assert config.auto_update.enabled is True


def test_load_config_auto_update_defaults_disabled(tmp_path):
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
    config = load_config(str(config_file))
    assert config.auto_update.enabled is False
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::test_load_config_auto_update_enabled tests/test_config.py::test_load_config_auto_update_defaults_disabled -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'auto_update'`

**Step 3: Implement `AutoUpdateConfig` and wire into `Config` + `load_config`**

In `src/remote_agent/config.py`, add the dataclass after `AgentConfig`:

```python
@dataclass
class AutoUpdateConfig:
    enabled: bool = False
```

Add field to `Config`:

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
    auto_update: AutoUpdateConfig = field(default_factory=AutoUpdateConfig)
```

In `load_config`, add before the `return Config(...)` statement:

```python
    return Config(
        ...
        logging=LoggingConfig(**logging_raw),
        auto_update=AutoUpdateConfig(**raw.get("auto_update", {})),
    )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/remote_agent/config.py tests/test_config.py
git commit -m "feat: add AutoUpdateConfig to config"
```

---

### Task 2: Create `AutoUpdater` class

**Files:**
- Create: `src/remote_agent/updater.py`
- Test: `tests/test_updater.py`

**Step 1: Write failing tests**

Create `tests/test_updater.py`:

```python
# tests/test_updater.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.updater import AutoUpdater
from remote_agent.exceptions import GitError


def _make_proc(stdout=b"", stderr=b"", returncode=0):
    proc = AsyncMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = returncode
    return proc


@patch("asyncio.create_subprocess_exec")
async def test_check_for_update_no_change(mock_exec):
    """Local and remote HEAD are the same — no update."""
    abc = b"abc123\n"
    mock_exec.side_effect = [
        _make_proc(),          # git fetch
        _make_proc(stdout=abc),  # git rev-parse HEAD
        _make_proc(stdout=abc),  # git rev-parse origin/main
    ]
    updater = AutoUpdater()
    assert await updater.check_for_update() is False


@patch("asyncio.create_subprocess_exec")
async def test_check_for_update_has_change(mock_exec):
    """Local and remote HEAD differ — update available."""
    mock_exec.side_effect = [
        _make_proc(),                       # git fetch
        _make_proc(stdout=b"abc123\n"),     # git rev-parse HEAD
        _make_proc(stdout=b"def456\n"),     # git rev-parse origin/main
    ]
    updater = AutoUpdater()
    assert await updater.check_for_update() is True


@patch("asyncio.create_subprocess_exec")
async def test_check_for_update_fetch_fails(mock_exec):
    """Fetch failure raises GitError."""
    mock_exec.return_value = _make_proc(stderr=b"network error", returncode=1)
    updater = AutoUpdater()
    with pytest.raises(GitError):
        await updater.check_for_update()


@patch("asyncio.create_subprocess_exec")
async def test_pull_update_success(mock_exec):
    """Pull + pip install both succeed."""
    mock_exec.side_effect = [
        _make_proc(),  # git pull
        _make_proc(),  # pip install
    ]
    updater = AutoUpdater()
    await updater.pull_update()
    assert mock_exec.call_count == 2
    # Verify git pull call
    args = mock_exec.call_args_list[0][0]
    assert args == ("git", "pull", "origin", "main")
    # Verify pip install call
    args = mock_exec.call_args_list[1][0]
    assert args == ("pip", "install", "-e", ".")


@patch("asyncio.create_subprocess_exec")
async def test_pull_update_git_fails(mock_exec):
    """Pull failure raises GitError."""
    mock_exec.return_value = _make_proc(stderr=b"merge conflict", returncode=1)
    updater = AutoUpdater()
    with pytest.raises(GitError):
        await updater.pull_update()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_updater.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'remote_agent.updater'`

**Step 3: Implement `AutoUpdater`**

Create `src/remote_agent/updater.py`:

```python
# src/remote_agent/updater.py
from __future__ import annotations
import asyncio
import logging

from remote_agent.exceptions import GitError

logger = logging.getLogger(__name__)


class AutoUpdater:
    async def check_for_update(self) -> bool:
        """Fetch origin/main and compare against local HEAD."""
        await self._run_git(["fetch", "origin", "main"])
        local = (await self._run_git(["rev-parse", "HEAD"])).strip()
        remote = (await self._run_git(["rev-parse", "origin/main"])).strip()
        if local != remote:
            logger.info("Update available: %s -> %s", local[:8], remote[:8])
            return True
        return False

    async def pull_update(self) -> None:
        """Pull latest main and reinstall dependencies."""
        await self._run_git(["pull", "origin", "main"])
        await self._run_cmd("pip", ["install", "-e", "."])
        logger.info("Update pulled and dependencies reinstalled")

    async def _run_git(self, args: list[str]) -> str:
        return await self._run_cmd("git", args)

    async def _run_cmd(self, cmd: str, args: list[str]) -> str:
        logger.debug("%s %s", cmd, " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise GitError(f"{cmd} {' '.join(args)} failed: {stderr.decode().strip()}")
        return stdout.decode()
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_updater.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/remote_agent/updater.py tests/test_updater.py
git commit -m "feat: add AutoUpdater class for self-update"
```

---

### Task 3: Wire updater into main loop

**Files:**
- Modify: `src/remote_agent/main.py:1-79` (add updater to App, modify loop)
- Test: `tests/test_main.py`

**Step 1: Write failing tests**

Add to `tests/test_main.py`:

```python
import sys
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.main import create_app, run


async def test_create_app_with_auto_update_enabled(tmp_path):
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
auto_update:
  enabled: true
""")
    app = await create_app(str(config_file))
    assert app.updater is not None
    await app.db.close()


async def test_create_app_without_auto_update(tmp_path):
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
    assert app.updater is None
    await app.db.close()


async def test_run_loop_exits_42_on_update(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
repos:
  - owner: "o"
    name: "r"
users:
  - "u"
polling:
  interval_seconds: 1
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
auto_update:
  enabled: true
""")
    mock_updater = AsyncMock()
    mock_updater.check_for_update.return_value = True

    with patch("remote_agent.main.Poller") as mock_poller_cls, \
         patch("remote_agent.main.Dispatcher") as mock_disp_cls, \
         patch("remote_agent.main.AutoUpdater", return_value=mock_updater), \
         pytest.raises(SystemExit) as exc_info:
        mock_poller_cls.return_value = AsyncMock()
        mock_disp_cls.return_value = AsyncMock()
        await run(str(config_file))

    assert exc_info.value.code == 42
    mock_updater.check_for_update.assert_called_once()
    mock_updater.pull_update.assert_called_once()


async def test_run_loop_continues_on_update_check_failure(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
repos:
  - owner: "o"
    name: "r"
users:
  - "u"
polling:
  interval_seconds: 1
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
auto_update:
  enabled: true
""")
    mock_updater = AsyncMock()
    call_count = 0

    async def check_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("network error")
        raise KeyboardInterrupt  # Stop loop on second call

    mock_updater.check_for_update.side_effect = check_side_effect

    with patch("remote_agent.main.Poller") as mock_poller_cls, \
         patch("remote_agent.main.Dispatcher") as mock_disp_cls, \
         patch("remote_agent.main.AutoUpdater", return_value=mock_updater), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_poller_cls.return_value = AsyncMock()
        mock_disp_cls.return_value = AsyncMock()
        try:
            await run(str(config_file))
        except KeyboardInterrupt:
            pass

    assert call_count == 2  # Loop continued past first failure
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main.py -v`
Expected: FAIL — `App` has no `updater` field, `AutoUpdater` not imported

**Step 3: Implement main loop changes**

In `src/remote_agent/main.py`:

1. Add import at top:
```python
import sys
from remote_agent.updater import AutoUpdater
```

2. Add `updater` field to `App`:
```python
@dataclass
class App:
    config: Config
    db: Database
    poller: Poller
    dispatcher: Dispatcher
    audit: AuditLogger | None = None
    updater: AutoUpdater | None = None
```

3. In `create_app`, add after the audit/poller/dispatcher setup:
```python
    updater = AutoUpdater() if config.auto_update.enabled else None

    return App(config=config, db=db, poller=poller, dispatcher=dispatcher, audit=audit, updater=updater)
```

4. In `run`, modify the while loop to add the update check after the try/except and before sleep:
```python
    try:
        while True:
            try:
                await app.poller.poll_once()
                await app.dispatcher.process_events()
            except Exception:
                logger.exception("Unexpected error in main loop")

            if app.updater:
                try:
                    if await app.updater.check_for_update():
                        await app.updater.pull_update()
                        logger.info("Update applied, restarting...")
                        sys.exit(42)
                except SystemExit:
                    raise
                except Exception:
                    logger.exception("Update check failed, continuing...")

            await asyncio.sleep(app.config.polling.interval_seconds)
    finally:
        if app.audit:
            app.audit.close()
        await app.db.close()
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `pytest -v`
Expected: ALL PASS (no regressions)

**Step 6: Commit**

```bash
git add src/remote_agent/main.py tests/test_main.py
git commit -m "feat: wire auto-updater into main poll loop"
```

---

### Task 4: Update README and systemd example

**Files:**
- Modify: `README.md` (systemd section + config section)

**Step 1: Add `RestartForceExitStatus=42` to the systemd example in README**

In the `[Service]` section of the example unit file, add:

```ini
RestartForceExitStatus=42
```

**Step 2: Document the `auto_update` config section**

Add to the config documentation in README:

```yaml
# Optional: auto-update (disabled by default)
auto_update:
  enabled: true  # Check for updates on main each poll cycle
```

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add auto-update config and systemd restart to README"
```

---

### Task 5: Final verification

**Step 1: Run full test suite**

Run: `pytest -v`
Expected: ALL PASS

**Step 2: Verify config with auto_update absent still works**

Run: `python3 -c "from remote_agent.config import load_config; c = load_config('config.yaml'); print(c.auto_update.enabled)"`
Expected: `False`

**Step 3: Verify import chain is clean**

Run: `python3 -c "from remote_agent.updater import AutoUpdater; print('OK')"`
Expected: `OK`
