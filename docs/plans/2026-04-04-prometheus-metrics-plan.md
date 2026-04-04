# Prometheus Metrics Endpoint — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace OTLP gRPC push telemetry with an in-process `/metrics` HTTP endpoint using prometheus_client + aiohttp.

**Architecture:** `telemetry.py` owns metric definitions and an aiohttp server lifecycle. `agent.py`, `github.py`, and `dispatcher.py` call thin `record_*` functions that are no-ops when telemetry is disabled. Config swaps `otlp_endpoint` for `metrics_port`.

**Tech Stack:** `prometheus_client>=0.20.0`, `aiohttp>=3.9.0`, Python 3.11+

**Design doc:** `docs/plans/2026-04-04-prometheus-metrics-design.md`

---

### Task 1: Update dependencies in pyproject.toml

**Files:**
- Modify: `pyproject.toml:20-25` (the `[telemetry]` optional-dependencies)

**Step 1: Replace the telemetry dependencies**

Change the `telemetry` extra from:

```toml
telemetry = [
    "opentelemetry-api>=1.20.0",
    "opentelemetry-sdk>=1.20.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.20.0",
    "opentelemetry-instrumentation-anthropic>=0.50.0",
]
```

To:

```toml
telemetry = [
    "prometheus_client>=0.20.0",
    "aiohttp>=3.9.0",
]
```

**Step 2: Install the new deps**

Run: `pip install -e ".[dev,telemetry]"`
Expected: Success, `prometheus_client` and `aiohttp` installed.

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: swap OTel deps for prometheus_client + aiohttp"
```

---

### Task 2: Update TelemetryConfig in config.py

**Files:**
- Modify: `src/remote_agent/config.py:60-63` (TelemetryConfig dataclass)
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_telemetry_config_metrics_port():
    """TelemetryConfig should have metrics_port, not otlp_endpoint."""
    from remote_agent.config import TelemetryConfig

    config = TelemetryConfig()
    assert config.metrics_port == 9090
    assert config.service_name == "remote-agent"
    assert config.enabled is False
    assert not hasattr(config, "otlp_endpoint")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_telemetry_config_metrics_port -v`
Expected: FAIL — `AttributeError: 'TelemetryConfig' object has no attribute 'metrics_port'`

**Step 3: Update TelemetryConfig**

In `src/remote_agent/config.py`, replace lines 60-63:

```python
@dataclass
class TelemetryConfig:
    enabled: bool = False
    metrics_port: int = 9090
    service_name: str = "remote-agent"
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_telemetry_config_metrics_port -v`
Expected: PASS

**Step 5: Run full config test suite for regressions**

Run: `pytest tests/test_config.py -v`
Expected: All pass. If any test references `otlp_endpoint`, update it to `metrics_port`.

**Step 6: Commit**

```bash
git add src/remote_agent/config.py tests/test_config.py
git commit -m "feat: replace otlp_endpoint with metrics_port in TelemetryConfig"
```

---

### Task 3: Rewrite telemetry.py — metric definitions and record functions

**Files:**
- Rewrite: `src/remote_agent/telemetry.py`
- Test: `tests/test_telemetry.py`

**Step 1: Write failing tests for the record functions**

Replace `tests/test_telemetry.py` entirely with:

```python
from __future__ import annotations
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from remote_agent.config import TelemetryConfig
import remote_agent.telemetry as telemetry_module


@pytest.fixture(autouse=True)
def reset_telemetry_state():
    telemetry_module._initialized = False
    telemetry_module._server_runner = None
    telemetry_module._server_site = None
    yield
    telemetry_module._initialized = False
    telemetry_module._server_runner = None
    telemetry_module._server_site = None


def test_record_query_metrics_disabled_is_noop():
    """record_query_metrics should silently do nothing when telemetry is not initialized."""
    # Should not raise even when prometheus_client is not configured
    telemetry_module.record_query_metrics(
        repo="owner/repo", phase="designing",
        model_usage={"claude-sonnet-4-6": {"inputTokens": 100, "outputTokens": 50, "costUSD": 0.01}},
        duration_ms=1000, duration_api_ms=800,
    )


def test_record_query_metrics_increments_counters():
    """record_query_metrics should increment all metric counters when enabled."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        model_usage = {
            "claude-sonnet-4-6": {
                "inputTokens": 1500,
                "outputTokens": 800,
                "cacheReadInputTokens": 100,
                "cacheCreationInputTokens": 200,
                "costUSD": 0.015,
            },
        }

        telemetry_module.record_query_metrics(
            repo="owner/repo", phase="designing",
            model_usage=model_usage,
            duration_ms=5000, duration_api_ms=4000,
        )

        # Verify session counter incremented
        val = telemetry_module.SESSION_COUNT.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6",
        )._value.get()
        assert val == 1.0

        # Verify token counters
        val = telemetry_module.TOKEN_USAGE.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6", type="input",
        )._value.get()
        assert val == 1500.0

        val = telemetry_module.TOKEN_USAGE.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6", type="cacheRead",
        )._value.get()
        assert val == 100.0

        # Verify cost counter
        val = telemetry_module.COST_USAGE.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6",
        )._value.get()
        assert val == 0.015

        # Verify active time counters
        val = telemetry_module.ACTIVE_TIME.labels(
            repo="owner/repo", phase="designing", type="agent",
        )._value.get()
        assert val == 5.0  # 5000ms -> 5s

        val = telemetry_module.ACTIVE_TIME.labels(
            repo="owner/repo", phase="designing", type="api",
        )._value.get()
        assert val == 4.0  # 4000ms -> 4s


def test_record_query_metrics_multiple_models():
    """record_query_metrics should handle multi-model usage (subagents)."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        model_usage = {
            "claude-sonnet-4-6": {
                "inputTokens": 1000, "outputTokens": 500,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                "costUSD": 0.01,
            },
            "claude-opus-4-6[1m]": {
                "inputTokens": 2000, "outputTokens": 1000,
                "cacheReadInputTokens": 500, "cacheCreationInputTokens": 100,
                "costUSD": 0.05,
            },
        }

        telemetry_module.record_query_metrics(
            repo="owner/repo", phase="implementing",
            model_usage=model_usage,
            duration_ms=10000, duration_api_ms=8000,
        )

        # Both models should have session counts
        val_sonnet = telemetry_module.SESSION_COUNT.labels(
            repo="owner/repo", phase="implementing", model="claude-sonnet-4-6",
        )._value.get()
        val_opus = telemetry_module.SESSION_COUNT.labels(
            repo="owner/repo", phase="implementing", model="claude-opus-4-6[1m]",
        )._value.get()
        assert val_sonnet == 1.0
        assert val_opus == 1.0


def test_record_query_error_increments_counter():
    """record_query_error should increment the error counter."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        telemetry_module.record_query_error(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6",
        )

        val = telemetry_module.QUERY_ERRORS.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6",
        )._value.get()
        assert val == 1.0


def test_record_pr_created_increments_counter():
    """record_pr_created should increment the PR counter."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        telemetry_module.record_pr_created(repo="owner/repo")

        val = telemetry_module.PR_COUNT.labels(repo="owner/repo")._value.get()
        assert val == 1.0


def test_record_phase_transition_increments_counter():
    """record_phase_transition should increment the transition counter."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        telemetry_module.record_phase_transition(
            repo="owner/repo", from_phase="designing", to_phase="design_review",
        )

        val = telemetry_module.PHASE_TRANSITIONS.labels(
            repo="owner/repo", from_phase="designing", to_phase="design_review",
        )._value.get()
        assert val == 1.0


def test_setup_telemetry_disabled_is_noop():
    """setup_telemetry should be a no-op when disabled."""
    config = TelemetryConfig(enabled=False)
    # Should not start any server
    telemetry_module.setup_telemetry(config)
    assert not telemetry_module._initialized


def test_setup_telemetry_missing_deps_logs_warning():
    """setup_telemetry should warn when prometheus_client is not installed."""
    config = TelemetryConfig(enabled=True)
    with (
        patch("remote_agent.telemetry.HAS_PROMETHEUS", False),
        patch("remote_agent.telemetry.logger") as mock_logger,
    ):
        telemetry_module.setup_telemetry(config)
        mock_logger.warning.assert_called_once()
        assert not telemetry_module._initialized
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telemetry.py -v`
Expected: FAIL — old telemetry.py doesn't have the new functions or metric objects.

**Step 3: Rewrite telemetry.py**

Replace `src/remote_agent/telemetry.py` entirely with:

```python
# src/remote_agent/telemetry.py
from __future__ import annotations

import logging

from remote_agent.config import TelemetryConfig

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, CollectorRegistry, generate_latest
    from aiohttp import web

    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False


_initialized = False
_server_runner: object | None = None
_server_site: object | None = None

# -- Metric definitions (safe to define even if prometheus_client missing) --

if HAS_PROMETHEUS:
    REGISTRY = CollectorRegistry()

    SESSION_COUNT = Counter(
        "remote_agent_session_count_total",
        "Count of agent query sessions",
        ["repo", "phase", "model"],
        registry=REGISTRY,
    )

    TOKEN_USAGE = Counter(
        "remote_agent_token_usage_total",
        "Number of tokens used",
        ["repo", "phase", "model", "type"],
        registry=REGISTRY,
    )

    COST_USAGE = Counter(
        "remote_agent_cost_usage_total",
        "Cost of agent queries in USD",
        ["repo", "phase", "model"],
        registry=REGISTRY,
    )

    ACTIVE_TIME = Counter(
        "remote_agent_active_time_total",
        "Total active time in seconds",
        ["repo", "phase", "type"],
        registry=REGISTRY,
    )

    QUERY_ERRORS = Counter(
        "remote_agent_query_errors_total",
        "Count of failed agent queries",
        ["repo", "phase", "model"],
        registry=REGISTRY,
    )

    PR_COUNT = Counter(
        "remote_agent_pull_request_count_total",
        "Number of pull requests created",
        ["repo"],
        registry=REGISTRY,
    )

    PHASE_TRANSITIONS = Counter(
        "remote_agent_phase_transitions_total",
        "Count of issue phase transitions",
        ["repo", "from_phase", "to_phase"],
        registry=REGISTRY,
    )


# -- Record functions (no-op when not initialized) --


def record_query_metrics(
    *, repo: str, phase: str, model_usage: dict | None,
    duration_ms: int, duration_api_ms: int,
) -> None:
    if not _initialized or not model_usage:
        return

    for model, usage in model_usage.items():
        SESSION_COUNT.labels(repo=repo, phase=phase, model=model).inc()
        COST_USAGE.labels(repo=repo, phase=phase, model=model).inc(
            usage.get("costUSD", 0),
        )
        for token_type, key in [
            ("input", "inputTokens"),
            ("output", "outputTokens"),
            ("cacheRead", "cacheReadInputTokens"),
            ("cacheCreation", "cacheCreationInputTokens"),
        ]:
            TOKEN_USAGE.labels(
                repo=repo, phase=phase, model=model, type=token_type,
            ).inc(usage.get(key, 0))

    ACTIVE_TIME.labels(repo=repo, phase=phase, type="agent").inc(duration_ms / 1000)
    ACTIVE_TIME.labels(repo=repo, phase=phase, type="api").inc(duration_api_ms / 1000)


def record_query_error(*, repo: str, phase: str, model: str) -> None:
    if not _initialized:
        return
    QUERY_ERRORS.labels(repo=repo, phase=phase, model=model).inc()


def record_pr_created(*, repo: str) -> None:
    if not _initialized:
        return
    PR_COUNT.labels(repo=repo).inc()


def record_phase_transition(*, repo: str, from_phase: str, to_phase: str) -> None:
    if not _initialized:
        return
    PHASE_TRANSITIONS.labels(repo=repo, from_phase=from_phase, to_phase=to_phase).inc()


# -- Server lifecycle --


async def _metrics_handler(request: web.Request) -> web.Response:
    body = generate_latest(REGISTRY)
    return web.Response(body=body, content_type="text/plain; version=0.0.4; charset=utf-8")


def setup_telemetry(config: TelemetryConfig) -> None:
    global _initialized

    if not config.enabled:
        return

    if _initialized:
        return

    if not HAS_PROMETHEUS:
        logger.warning(
            "Telemetry enabled but prometheus_client/aiohttp not installed. "
            "Install with: pip install -e '.[telemetry]'"
        )
        return

    _initialized = True
    logger.info(
        "Telemetry enabled: /metrics on port %d as %s",
        config.metrics_port, config.service_name,
    )


async def start_metrics_server(config: TelemetryConfig) -> None:
    global _server_runner, _server_site

    if not _initialized:
        return

    app = web.Application()
    app.router.add_get("/metrics", _metrics_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.metrics_port)
    await site.start()

    _server_runner = runner
    _server_site = site

    logger.info("Metrics server listening on port %d", config.metrics_port)


async def shutdown_telemetry() -> None:
    global _server_runner, _server_site, _initialized

    if _server_runner:
        await _server_runner.cleanup()
        _server_runner = None
        _server_site = None
        logger.info("Metrics server stopped")
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telemetry.py -v`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/remote_agent/telemetry.py tests/test_telemetry.py
git commit -m "feat: rewrite telemetry with prometheus_client metrics and aiohttp server"
```

---

### Task 4: Wire telemetry into main.py (server lifecycle)

**Files:**
- Modify: `src/remote_agent/main.py:58` and `main.py:83-86` (finally block)
- Test: `tests/test_main.py`

**Step 1: Check existing main.py tests**

Run: `pytest tests/test_main.py -v`
Expected: All pass (baseline).

**Step 2: Update main.py**

In `src/remote_agent/main.py`, add the import for the new async functions. Change the import on line 17 from:

```python
from remote_agent.telemetry import setup_telemetry
```

To:

```python
from remote_agent.telemetry import setup_telemetry, start_metrics_server, shutdown_telemetry
```

After line 58 (`setup_telemetry(app.config.telemetry)`), add:

```python
    await start_metrics_server(app.config.telemetry)
```

In the `finally` block (after line 85 `await app.db.close()`), add:

```python
        await shutdown_telemetry()
```

The `run()` function should look like:

```python
async def run(config_path: str = "config.yaml"):
    # Phase 1: minimal console logging until config is available
    logging.basicConfig(level=logging.INFO)

    app = await create_app(config_path)

    # Phase 2: reconfigure with structured JSON logging
    from remote_agent.logging_config import setup_logging
    setup_logging(app.config)

    setup_telemetry(app.config.telemetry)
    await start_metrics_server(app.config.telemetry)

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
        await shutdown_telemetry()
```

**Step 3: Run main.py tests**

Run: `pytest tests/test_main.py -v`
Expected: All pass. If any test mocks `setup_telemetry`, it may need to also mock `start_metrics_server` and `shutdown_telemetry`. Check for `ImportError` or `AttributeError` in failures and patch accordingly.

**Step 4: Commit**

```bash
git add src/remote_agent/main.py tests/test_main.py
git commit -m "feat: wire metrics server start/shutdown into main.py lifecycle"
```

---

### Task 5: Instrument agent.py._run_query with telemetry calls

**Files:**
- Modify: `src/remote_agent/agent.py:158-211` (`_run_query` method)
- Test: `tests/test_agent.py` (if it exists, add test; otherwise create)

**Step 1: Write the failing test**

Add a test (in existing agent test file or new `tests/test_agent_telemetry.py`):

```python
from __future__ import annotations
from unittest.mock import patch, AsyncMock, MagicMock
from dataclasses import dataclass
from remote_agent.agent import AgentService, AgentResult


@dataclass
class FakeResultMessage:
    subtype: str = "result"
    duration_ms: int = 5000
    duration_api_ms: int = 4000
    is_error: bool = False
    num_turns: int = 3
    session_id: str = "test-session"
    total_cost_usd: float = 0.05
    usage: dict = None
    result: str = "done"
    model_usage: dict = None

    def __post_init__(self):
        if self.usage is None:
            self.usage = {"input_tokens": 1500, "output_tokens": 800}
        if self.model_usage is None:
            self.model_usage = {
                "claude-sonnet-4-6": {
                    "inputTokens": 1500,
                    "outputTokens": 800,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                    "costUSD": 0.05,
                }
            }


async def test_run_query_calls_record_query_metrics():
    """_run_query should call record_query_metrics with model_usage from ResultMessage."""
    mock_db = MagicMock()
    mock_db.create_agent_run = AsyncMock(return_value=1)
    mock_db.get_latest_session_for_phase = AsyncMock(return_value=None)
    mock_db.complete_agent_run = AsyncMock()

    mock_config = MagicMock()
    service = AgentService(mock_config, mock_db)

    msg = FakeResultMessage()

    async def fake_query(**kwargs):
        yield msg

    mock_options = MagicMock()
    mock_options.model = "sonnet"

    with (
        patch("remote_agent.agent.query", side_effect=fake_query),
        patch("remote_agent.agent.ResultMessage", FakeResultMessage),
        patch("remote_agent.telemetry.record_query_metrics") as mock_record,
    ):
        result = await service._run_query("test prompt", mock_options, issue_id=1, phase="designing")

        mock_record.assert_called_once_with(
            repo="",
            phase="designing",
            model_usage=msg.model_usage,
            duration_ms=5000,
            duration_api_ms=4000,
        )
```

**Step 2: Run to verify it fails**

Run: `pytest tests/test_agent_telemetry.py::test_run_query_calls_record_query_metrics -v`
Expected: FAIL — `record_query_metrics` is not yet called in `_run_query`.

**Step 3: Add telemetry calls to _run_query**

In `src/remote_agent/agent.py`, add at the top (after existing imports):

```python
from remote_agent.telemetry import record_query_metrics, record_query_error
```

In the `_run_query` method, add these variables after `output_tokens = 0` (line 177):

```python
        model_usage = None
        duration_ms = 0
        duration_api_ms = 0
```

Inside the `if isinstance(message, ResultMessage):` block (after line 190), add:

```python
                    model_usage = message.model_usage
                    duration_ms = message.duration_ms
                    duration_api_ms = message.duration_api_ms
```

After the `logger.info("Completed %s query...")` line (line 192), add:

```python
            record_query_metrics(
                repo=getattr(getattr(self.config, '_current_repo', None), '', ''),
                phase=phase,
                model_usage=model_usage,
                duration_ms=duration_ms,
                duration_api_ms=duration_api_ms,
            )
```

**WAIT** — `_run_query` doesn't have access to `repo`. The callers (`run_designing`, etc.) don't pass repo info to `_run_query`. We need to thread `repo` through.

Update `_run_query` signature to accept `repo: str = ""`:

```python
    async def _run_query(self, prompt: str, options, issue_id: int, phase: str,
                          allow_resume: bool = False, repo: str = "") -> AgentResult:
```

Add the telemetry call after the `logger.info("Completed...")` line:

```python
            record_query_metrics(
                repo=repo, phase=phase, model_usage=model_usage,
                duration_ms=duration_ms, duration_api_ms=duration_api_ms,
            )
```

In the `except` block, before `raise AgentError(...)`:

```python
            record_query_error(repo=repo, phase=phase, model=getattr(options, "model", "unknown"))
```

Update all callers of `_run_query` to pass `repo`. Each caller has access to `issue_number` and related identifiers but not `owner/name` directly. The simplest approach: add a helper that builds the repo string, and thread it through the public methods.

Add to `AgentService.__init__`:

```python
    def _repo_label(self) -> str:
        """Build repo label from first configured repo (single-repo service)."""
        if self.config.repos:
            r = self.config.repos[0]
            return f"{r.owner}/{r.name}"
        return ""
```

Then in each `_run_query` call, pass `repo=self._repo_label()`. For example in `run_designing` (line 68):

```python
        return await self._run_query(user_prompt, options, issue_id, phase="designing", allow_resume=True, repo=self._repo_label())
```

Same pattern for `run_planning` (line 91), `run_implementation` (line 116), and `answer_question` (line 155).

**Step 4: Update the test to match the actual repo value**

Update the test's assertion to use `repo=""` or mock `config.repos`.

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_agent_telemetry.py -v`
Expected: PASS

**Step 6: Run full test suite**

Run: `pytest -v`
Expected: All pass. Fix any test that calls `_run_query` and now fails due to the new import or signature.

**Step 7: Commit**

```bash
git add src/remote_agent/agent.py tests/test_agent_telemetry.py
git commit -m "feat: instrument _run_query with Prometheus telemetry recording"
```

---

### Task 6: Instrument github.py.create_pr with telemetry

**Files:**
- Modify: `src/remote_agent/github.py:100-113` (`create_pr` method)

**Step 1: Add telemetry call to create_pr**

In `src/remote_agent/github.py`, add at the top:

```python
from remote_agent.telemetry import record_pr_created
```

In `create_pr`, after the `return` value is computed (line 113), restructure to call telemetry before returning. Replace the `create_pr` method:

```python
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
        pr_number = int(pr_url.rstrip("/").split("/")[-1])
        record_pr_created(repo=f"{owner}/{repo}")
        return pr_number
```

**Step 2: Run existing GitHub tests**

Run: `pytest tests/ -k "github" -v`
Expected: All pass. The `record_pr_created` is a no-op when telemetry is not initialized, so no mocking needed.

**Step 3: Commit**

```bash
git add src/remote_agent/github.py
git commit -m "feat: record PR creation metric in github.py"
```

---

### Task 7: Instrument dispatcher.py phase transitions

**Files:**
- Modify: `src/remote_agent/dispatcher.py:110-112` (success path in `_process_event`)

**Step 1: Add telemetry call**

In `src/remote_agent/dispatcher.py`, add at the top:

```python
from remote_agent.telemetry import record_phase_transition
```

In `_process_event`, after `await self.db.update_issue_phase(issue.id, result.next_phase)` (line 112), add:

```python
            record_phase_transition(
                repo=f"{issue.repo_owner}/{issue.repo_name}",
                from_phase=issue.phase,
                to_phase=result.next_phase,
            )
```

**Step 2: Run dispatcher tests**

Run: `pytest tests/ -k "dispatch" -v`
Expected: All pass. `record_phase_transition` is a no-op when not initialized.

**Step 3: Commit**

```bash
git add src/remote_agent/dispatcher.py
git commit -m "feat: record phase transition metric in dispatcher"
```

---

### Task 8: Integration test — /metrics endpoint serves Prometheus format

**Files:**
- Create: `tests/test_telemetry_integration.py`

**Step 1: Write the integration test**

```python
from __future__ import annotations
import pytest
import remote_agent.telemetry as telemetry_module
from remote_agent.config import TelemetryConfig


@pytest.fixture
def telemetry_config():
    return TelemetryConfig(enabled=True, metrics_port=0, service_name="test-agent")


@pytest.fixture(autouse=True)
def reset_telemetry():
    telemetry_module._initialized = False
    telemetry_module._server_runner = None
    telemetry_module._server_site = None
    yield
    telemetry_module._initialized = False
    telemetry_module._server_runner = None
    telemetry_module._server_site = None


async def test_metrics_endpoint_serves_prometheus_format(telemetry_config):
    """The /metrics endpoint should return Prometheus text format."""
    telemetry_module.setup_telemetry(telemetry_config)

    # Record some metrics
    telemetry_module.record_query_metrics(
        repo="owner/repo", phase="designing",
        model_usage={
            "claude-sonnet-4-6": {
                "inputTokens": 100, "outputTokens": 50,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                "costUSD": 0.01,
            },
        },
        duration_ms=1000, duration_api_ms=800,
    )

    # Start the server
    await telemetry_module.start_metrics_server(telemetry_config)

    try:
        # Get the actual port (when port=0, OS assigns one)
        import aiohttp
        runner = telemetry_module._server_runner
        site = telemetry_module._server_site
        # Access the bound socket to find actual port
        sock = site._server.sockets[0]
        port = sock.getsockname()[1]

        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://localhost:{port}/metrics") as resp:
                assert resp.status == 200
                body = await resp.text()
                assert "remote_agent_session_count_total" in body
                assert "remote_agent_token_usage_total" in body
                assert "remote_agent_cost_usage_total" in body
                assert 'repo="owner/repo"' in body
                assert 'model="claude-sonnet-4-6"' in body
    finally:
        await telemetry_module.shutdown_telemetry()
```

**Note:** Using `metrics_port=0` lets the OS assign a free port, avoiding port conflicts in CI.

**Step 2: Run the integration test**

Run: `pytest tests/test_telemetry_integration.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_telemetry_integration.py
git commit -m "test: add integration test for /metrics Prometheus endpoint"
```

---

### Task 9: Run full test suite and fix regressions

**Step 1: Run all tests**

Run: `pytest -v`
Expected: All pass. If any failures, investigate and fix.

**Step 2: Common regressions to watch for**

- `tests/test_main.py`: may need to mock `start_metrics_server` and `shutdown_telemetry` imports
- `tests/test_telemetry.py`: the counter `._value.get()` pattern works for `prometheus_client` Counter objects — if it doesn't, use `REGISTRY.get_sample_value()` instead
- Any test importing `TelemetryConfig` with `otlp_endpoint=` kwarg needs updating

**Step 3: Commit any fixes**

```bash
git add -u
git commit -m "fix: resolve test regressions from telemetry refactor"
```

---

### Task 10: Clean up old design doc reference

**Step 1: Mark old design as superseded**

Add a note at the top of `docs/plans/2026-04-03-otel-metrics-design.md`:

```markdown
> **SUPERSEDED** by [2026-04-04-prometheus-metrics-design.md](2026-04-04-prometheus-metrics-design.md)
```

**Step 2: Commit**

```bash
git add docs/plans/2026-04-03-otel-metrics-design.md
git commit -m "docs: mark old OTel design as superseded"
```
