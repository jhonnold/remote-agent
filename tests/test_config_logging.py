# tests/test_config_logging.py
from __future__ import annotations
from pathlib import Path
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
    assert Path(config.logging.file).name == "custom.log"
    assert Path(config.logging.file).is_absolute()
    assert Path(config.logging.audit_file).name == "custom-audit.jsonl"
    assert Path(config.logging.audit_file).is_absolute()


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
    assert Path(config.logging.file).name == "remote-agent.log"
    assert Path(config.logging.audit_file).name == "audit.jsonl"
