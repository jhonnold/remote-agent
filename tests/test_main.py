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
