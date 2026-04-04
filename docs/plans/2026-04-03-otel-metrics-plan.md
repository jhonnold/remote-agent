# OTel Metrics Export Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire up `opentelemetry-instrumentation-anthropic` so every `query()` call automatically emits OTel spans exported via OTLP to an external collector.

**Architecture:** A new `telemetry.py` module configures a `TracerProvider` with an OTLP exporter and calls `AnthropicInstrumentor().instrument()` at startup. Config is opt-in via a `telemetry` section in `config.yaml`. No changes to `agent.py` — the instrumentor patches `query()` automatically.

**Tech Stack:** `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-instrumentation-anthropic`

---

### Task 1: Add TelemetryConfig to config.py

**Files:**
- Modify: `src/remote_agent/config.py:55-69` (add dataclass before `Config`, add field to `Config`)
- Modify: `src/remote_agent/config.py:108-118` (wire into `load_config`)
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_load_config_telemetry_defaults(tmp_path):
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
    assert config.telemetry.enabled is False
    assert config.telemetry.otlp_endpoint == "http://localhost:4317"
    assert config.telemetry.service_name == "remote-agent"


def test_load_config_telemetry_custom(tmp_path):
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
telemetry:
  enabled: true
  otlp_endpoint: "http://collector:4317"
  service_name: "my-agent"
""")
    config = load_config(str(config_file))
    assert config.telemetry.enabled is True
    assert config.telemetry.otlp_endpoint == "http://collector:4317"
    assert config.telemetry.service_name == "my-agent"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_load_config_telemetry_defaults tests/test_config.py::test_load_config_telemetry_custom -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'telemetry'`

**Step 3: Write minimal implementation**

In `src/remote_agent/config.py`, add the `TelemetryConfig` dataclass after `AutoUpdateConfig`:

```python
@dataclass
class TelemetryConfig:
    enabled: bool = False
    otlp_endpoint: str = "http://localhost:4317"
    service_name: str = "remote-agent"
```

Add to the `Config` dataclass:

```python
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
```

In `load_config`, add before the `return Config(...)` statement:

```python
    telemetry=TelemetryConfig(**raw.get("telemetry", {})),
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/remote_agent/config.py tests/test_config.py
git commit -m "feat: add TelemetryConfig for OTel metrics export"
```

---

### Task 2: Add OTel dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml:9-13` (add telemetry optional dependency group)

**Step 1: Add telemetry dependency group**

Add a new optional dependency group in `pyproject.toml` under `[project.optional-dependencies]`:

```toml
telemetry = [
    "opentelemetry-api>=1.20.0",
    "opentelemetry-sdk>=1.20.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.20.0",
    "opentelemetry-instrumentation-anthropic>=0.1.0",
]
```

This keeps OTel deps optional — users install with `pip install -e ".[telemetry]"`.

**Step 2: Verify install**

Run: `pip install -e ".[telemetry]"`
Expected: All OTel packages install successfully

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add opentelemetry optional dependency group"
```

---

### Task 3: Create telemetry.py module

**Files:**
- Create: `src/remote_agent/telemetry.py`
- Test: `tests/test_telemetry.py`

**Step 1: Write the failing tests**

Create `tests/test_telemetry.py`:

```python
from unittest.mock import patch, MagicMock
from remote_agent.config import TelemetryConfig
from remote_agent.telemetry import setup_telemetry


def test_setup_telemetry_disabled_is_noop():
    config = TelemetryConfig(enabled=False)
    with patch("remote_agent.telemetry.TracerProvider", create=True) as mock_tp:
        setup_telemetry(config)
        mock_tp.assert_not_called()


def test_setup_telemetry_enabled_configures_provider():
    config = TelemetryConfig(
        enabled=True,
        otlp_endpoint="http://localhost:4317",
        service_name="test-agent",
    )
    mock_instrumentor = MagicMock()
    with (
        patch("remote_agent.telemetry.TracerProvider") as mock_tp_cls,
        patch("remote_agent.telemetry.OTLPSpanExporter") as mock_exporter_cls,
        patch("remote_agent.telemetry.BatchSpanProcessor") as mock_bsp_cls,
        patch("remote_agent.telemetry.trace") as mock_trace,
        patch("remote_agent.telemetry.AnthropicInstrumentor", return_value=mock_instrumentor) as mock_instr_cls,
        patch("remote_agent.telemetry.Resource") as mock_resource_cls,
    ):
        setup_telemetry(config)

        # Verify provider was created and set
        mock_tp_cls.assert_called_once()
        mock_trace.set_tracer_provider.assert_called_once()

        # Verify exporter uses configured endpoint
        mock_exporter_cls.assert_called_once_with(endpoint="http://localhost:4317")

        # Verify instrumentor was called
        mock_instrumentor.instrument.assert_called_once()


def test_setup_telemetry_missing_deps_logs_warning():
    config = TelemetryConfig(enabled=True)
    with (
        patch("remote_agent.telemetry.HAS_OTEL", False),
        patch("remote_agent.telemetry.logger") as mock_logger,
    ):
        setup_telemetry(config)
        mock_logger.warning.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telemetry.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError`

**Step 3: Write the implementation**

Create `src/remote_agent/telemetry.py`:

```python
# src/remote_agent/telemetry.py
from __future__ import annotations
import logging

from remote_agent.config import TelemetryConfig

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.instrumentation.claude_agent_sdk import AnthropicInstrumentor
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False


def setup_telemetry(config: TelemetryConfig) -> None:
    if not config.enabled:
        return

    if not HAS_OTEL:
        logger.warning(
            "Telemetry enabled but opentelemetry packages not installed. "
            "Install with: pip install -e '.[telemetry]'"
        )
        return

    resource = Resource.create({"service.name": config.service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=config.otlp_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    AnthropicInstrumentor().instrument()

    logger.info(
        "Telemetry enabled: exporting to %s as %s",
        config.otlp_endpoint, config.service_name,
    )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telemetry.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add src/remote_agent/telemetry.py tests/test_telemetry.py
git commit -m "feat: add telemetry module with OTel SDK instrumentor setup"
```

---

### Task 4: Wire setup_telemetry into main.py

**Files:**
- Modify: `src/remote_agent/main.py:51-56` (add telemetry setup call after config loads)
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

Add to `tests/test_main.py`. The existing tests use real config files via `tmp_path` and patch constructors at the module level. Follow the same pattern:

```python
async def test_run_calls_setup_telemetry(tmp_path):
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
telemetry:
  enabled: true
  otlp_endpoint: "http://collector:4317"
  service_name: "test-agent"
""")
    with patch("remote_agent.main.Poller") as mock_poller_cls, \
         patch("remote_agent.main.Dispatcher") as mock_disp_cls, \
         patch("remote_agent.main.setup_telemetry") as mock_setup_tel:
        mock_poller_cls.return_value = AsyncMock()
        mock_disp = AsyncMock()
        mock_disp.process_events.side_effect = KeyboardInterrupt
        mock_disp_cls.return_value = mock_disp
        try:
            await run(str(config_file))
        except KeyboardInterrupt:
            pass

        mock_setup_tel.assert_called_once()
        call_arg = mock_setup_tel.call_args[0][0]
        assert call_arg.enabled is True
        assert call_arg.otlp_endpoint == "http://collector:4317"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py::test_run_calls_setup_telemetry -v`
Expected: FAIL — `setup_telemetry` not imported or called

**Step 3: Write minimal implementation**

In `src/remote_agent/main.py`, add the import and call. After line 55 (`setup_logging(app.config)`), add:

```python
    from remote_agent.telemetry import setup_telemetry
    setup_telemetry(app.config.telemetry)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -v`
Expected: All tests PASS

**Step 5: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add src/remote_agent/main.py tests/test_main.py
git commit -m "feat: wire telemetry setup into main startup"
```

---

### Task 5: Final verification and cleanup

**Step 1: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS

**Step 2: Verify telemetry install works**

Run: `pip install -e ".[telemetry]" && python -c "from remote_agent.telemetry import setup_telemetry; print('OK')"`
Expected: Prints `OK`

**Step 3: Verify service starts with telemetry disabled (default)**

Run: `python -c "from remote_agent.config import TelemetryConfig; from remote_agent.telemetry import setup_telemetry; setup_telemetry(TelemetryConfig()); print('noop OK')"`
Expected: Prints `noop OK` (no errors, no OTel setup)

**Step 4: Commit plan doc**

```bash
git add docs/plans/2026-04-03-otel-metrics-plan.md
git commit -m "docs: add OTel metrics implementation plan"
```
