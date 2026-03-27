# tests/test_logging_config.py
from __future__ import annotations
import json
import logging
import pytest

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
