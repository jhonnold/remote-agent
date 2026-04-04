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


def test_agent_config_default_orchestrator_model_is_sonnet():
    from remote_agent.config import AgentConfig
    config = AgentConfig()
    assert config.orchestrator_model == "sonnet"
