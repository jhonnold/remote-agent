# tests/test_main.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.main import create_app, run


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


async def test_run_loop_continues_on_pull_failure(tmp_path):
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
            return True  # Update available
        raise KeyboardInterrupt  # Stop loop on second call

    mock_updater.check_for_update.side_effect = check_side_effect
    mock_updater.pull_update.side_effect = RuntimeError("pip install failed")

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

    assert call_count == 2  # Loop continued past pull failure
    mock_updater.pull_update.assert_called_once()


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
