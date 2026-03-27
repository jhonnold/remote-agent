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
