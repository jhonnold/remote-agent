# src/remote_agent/audit.py
from __future__ import annotations
import json

from remote_agent.db import Database
from remote_agent.logging_config import current_issue_id, current_event_id


class AuditLogger:
    def __init__(self, db: Database, audit_file_path: str):
        self._db = db
        self._file = open(audit_file_path, "a")

    def close(self) -> None:
        self._file.flush()
        self._file.close()

    async def log(
        self,
        category: str,
        action: str,
        *,
        issue_id: int | None = None,
        event_id: int | None = None,
        detail: dict | None = None,
        duration_ms: int | None = None,
        success: bool,
        error_message: str | None = None,
    ) -> None:
        # Use ContextVar defaults when not passed explicitly
        if issue_id is None:
            issue_id = current_issue_id.get(None)
        if event_id is None:
            event_id = current_event_id.get(None)

        record = {
            "category": category,
            "action": action,
            "issue_id": issue_id,
            "event_id": event_id,
            "detail": detail,
            "duration_ms": duration_ms,
            "success": success,
            "error_message": error_message,
        }

        # File first -- if this fails, DB write is skipped
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

        # Then DB (via Database public method)
        detail_json = json.dumps(detail) if detail else None
        await self._db.create_audit_entry(
            issue_id=issue_id, event_id=event_id, category=category,
            action=action, detail=detail_json, duration_ms=duration_ms,
            success=int(success), error_message=error_message,
        )
