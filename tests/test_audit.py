# tests/test_audit.py
from __future__ import annotations
import json
import pytest
from unittest.mock import patch

from remote_agent.audit import AuditLogger
from remote_agent.db import Database
from remote_agent.logging_config import current_issue_id, current_event_id, current_operation_id


@pytest.fixture(autouse=True)
def reset_context_vars():
    yield
    current_issue_id.set(None)
    current_event_id.set(None)
    current_operation_id.set(None)


@pytest.fixture
async def db(tmp_path):
    database = await Database.initialize(str(tmp_path / "test.db"))
    yield database
    await database.close()


@pytest.fixture
def audit_file(tmp_path):
    return tmp_path / "audit.jsonl"


@pytest.fixture
def audit(db, audit_file):
    a = AuditLogger(db, str(audit_file))
    yield a
    a.close()


async def test_log_writes_to_file_and_db(audit, db, audit_file):
    await audit.log("phase_transition", "plan_created", issue_id=1, success=True)

    # Check JSONL file
    lines = audit_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["category"] == "phase_transition"
    assert data["action"] == "plan_created"
    assert data["issue_id"] == 1
    assert data["success"] is True

    # Check DB
    cursor = await db._conn.execute("SELECT * FROM audit_log")
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["category"] == "phase_transition"
    assert rows[0]["success"] == 1


async def test_log_uses_context_vars_as_defaults(audit, audit_file):
    current_issue_id.set(42)
    current_event_id.set(7)
    await audit.log("git_op", "branch_push", success=True)

    data = json.loads(audit_file.read_text().strip())
    assert data["issue_id"] == 42
    assert data["event_id"] == 7


async def test_log_explicit_args_override_context_vars(audit, audit_file):
    current_issue_id.set(42)
    await audit.log("git_op", "branch_push", issue_id=99, success=True)

    data = json.loads(audit_file.read_text().strip())
    assert data["issue_id"] == 99


async def test_log_with_duration(audit, db, audit_file):
    await audit.log("github_api", "create_pr", issue_id=1, duration_ms=1500, success=True)

    data = json.loads(audit_file.read_text().strip())
    assert data["duration_ms"] == 1500

    cursor = await db._conn.execute("SELECT duration_ms FROM audit_log")
    row = await cursor.fetchone()
    assert row["duration_ms"] == 1500


async def test_log_without_duration_stores_null(audit, db):
    await audit.log("phase_transition", "started", issue_id=1, success=True)

    cursor = await db._conn.execute("SELECT duration_ms FROM audit_log")
    row = await cursor.fetchone()
    assert row["duration_ms"] is None


async def test_log_with_error(audit, audit_file):
    await audit.log("phase_transition", "handler_failed", issue_id=1,
                     success=False, error_message="Agent crashed")

    data = json.loads(audit_file.read_text().strip())
    assert data["success"] is False
    assert data["error_message"] == "Agent crashed"


async def test_close_flushes_file(db, tmp_path):
    audit_file = tmp_path / "audit2.jsonl"
    audit = AuditLogger(db, str(audit_file))
    await audit.log("phase_transition", "test", issue_id=1, success=True)
    audit.close()
    assert audit_file.read_text().strip() != ""


async def test_file_write_failure_prevents_db_write(db, tmp_path):
    audit_file = tmp_path / "audit3.jsonl"
    audit = AuditLogger(db, str(audit_file))

    with patch.object(audit._file, "write", side_effect=IOError("disk full")):
        with pytest.raises(IOError):
            await audit.log("phase_transition", "test", issue_id=1, success=True)

    # DB should NOT have the record
    cursor = await db._conn.execute("SELECT COUNT(*) as cnt FROM audit_log")
    row = await cursor.fetchone()
    assert row["cnt"] == 0
    audit.close()


async def test_context_vars_none_when_unset(audit, audit_file):
    await audit.log("phase_transition", "test", success=True)
    data = json.loads(audit_file.read_text().strip())
    assert data.get("issue_id") is None
    assert data.get("event_id") is None
