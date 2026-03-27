# src/remote_agent/logging_config.py
from __future__ import annotations
import json
import logging
import logging.handlers
import os
from contextvars import ContextVar

from remote_agent.config import Config

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
