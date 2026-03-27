# src/remote_agent/db.py
from __future__ import annotations
import json
from pathlib import Path

import aiosqlite

from remote_agent.models import Issue, Event, AgentRun

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_owner TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    phase TEXT NOT NULL DEFAULT 'new',
    branch_name TEXT,
    pr_number INTEGER,
    workspace_path TEXT,
    plan_approved INTEGER DEFAULT 0,
    plan_commit_hash TEXT,
    last_comment_id INTEGER DEFAULT 0,
    budget_notified INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(repo_owner, repo_name, issue_number)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    processed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    phase TEXT NOT NULL,
    session_id TEXT,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    result TEXT,
    cost_usd REAL DEFAULT 0.0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    issue_id INTEGER,
    event_id INTEGER,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    duration_ms INTEGER,
    success INTEGER NOT NULL,
    error_message TEXT,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
);
CREATE INDEX IF NOT EXISTS idx_audit_log_issue_id ON audit_log(issue_id);
"""


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    @classmethod
    async def initialize(cls, db_path: str) -> Database:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await conn.executescript(SCHEMA)
        await conn.commit()
        return cls(conn)

    async def close(self):
        await self._conn.close()

    # --- Issues ---

    async def create_issue(self, repo_owner: str, repo_name: str, issue_data: dict) -> int | None:
        try:
            cursor = await self._conn.execute(
                "INSERT INTO issues (repo_owner, repo_name, issue_number, title, body) VALUES (?, ?, ?, ?, ?)",
                (repo_owner, repo_name, issue_data["number"], issue_data["title"], issue_data.get("body", "")),
            )
            await self._conn.commit()
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            return None

    async def get_issue(self, repo_owner: str, repo_name: str, issue_number: int) -> Issue | None:
        cursor = await self._conn.execute(
            "SELECT * FROM issues WHERE repo_owner = ? AND repo_name = ? AND issue_number = ?",
            (repo_owner, repo_name, issue_number),
        )
        row = await cursor.fetchone()
        return self._row_to_issue(row) if row else None

    async def get_issue_by_id(self, issue_id: int) -> Issue | None:
        cursor = await self._conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,))
        row = await cursor.fetchone()
        return self._row_to_issue(row) if row else None

    async def get_issues_awaiting_comment(self, repo_owner: str, repo_name: str) -> list[Issue]:
        cursor = await self._conn.execute(
            "SELECT * FROM issues WHERE repo_owner = ? AND repo_name = ? AND phase IN ('plan_review', 'code_review', 'error')",
            (repo_owner, repo_name),
        )
        rows = await cursor.fetchall()
        return [self._row_to_issue(r) for r in rows]

    async def get_active_issues(self) -> list[Issue]:
        cursor = await self._conn.execute(
            "SELECT * FROM issues WHERE phase IN ('planning', 'implementing')"
        )
        rows = await cursor.fetchall()
        return [self._row_to_issue(r) for r in rows]

    async def update_issue_phase(self, issue_id: int, phase: str):
        await self._conn.execute(
            "UPDATE issues SET phase = ?, updated_at = datetime('now') WHERE id = ?",
            (phase, issue_id),
        )
        await self._conn.commit()

    async def update_issue_branch(self, issue_id: int, branch: str):
        await self._conn.execute(
            "UPDATE issues SET branch_name = ?, updated_at = datetime('now') WHERE id = ?",
            (branch, issue_id),
        )
        await self._conn.commit()

    async def update_issue_pr(self, issue_id: int, pr_number: int):
        await self._conn.execute(
            "UPDATE issues SET pr_number = ?, updated_at = datetime('now') WHERE id = ?",
            (pr_number, issue_id),
        )
        await self._conn.commit()

    async def update_issue_workspace(self, issue_id: int, workspace_path: str):
        await self._conn.execute(
            "UPDATE issues SET workspace_path = ?, updated_at = datetime('now') WHERE id = ?",
            (workspace_path, issue_id),
        )
        await self._conn.commit()

    async def set_plan_approved(self, issue_id: int, approved: bool):
        await self._conn.execute(
            "UPDATE issues SET plan_approved = ?, updated_at = datetime('now') WHERE id = ?",
            (int(approved), issue_id),
        )
        await self._conn.commit()

    async def set_plan_commit_hash(self, issue_id: int, commit_hash: str):
        await self._conn.execute(
            "UPDATE issues SET plan_commit_hash = ?, updated_at = datetime('now') WHERE id = ?",
            (commit_hash, issue_id),
        )
        await self._conn.commit()

    async def update_issue_error(self, issue_id: int, error_message: str):
        await self._conn.execute(
            "UPDATE issues SET error_message = ?, updated_at = datetime('now') WHERE id = ?",
            (error_message, issue_id),
        )
        await self._conn.commit()

    async def set_budget_notified(self, issue_id: int, notified: bool):
        await self._conn.execute(
            "UPDATE issues SET budget_notified = ?, updated_at = datetime('now') WHERE id = ?",
            (int(notified), issue_id),
        )
        await self._conn.commit()

    async def update_last_comment_id(self, issue_id: int, comment_id: int):
        await self._conn.execute(
            "UPDATE issues SET last_comment_id = ?, updated_at = datetime('now') WHERE id = ?",
            (comment_id, issue_id),
        )
        await self._conn.commit()

    # --- Events ---

    async def create_event(self, issue_id: int, event_type: str, payload: dict | None = None):
        await self._conn.execute(
            "INSERT INTO events (issue_id, event_type, payload) VALUES (?, ?, ?)",
            (issue_id, event_type, json.dumps(payload or {})),
        )
        await self._conn.commit()

    async def create_comment_events(self, issue_id: int, comments: list[dict]):
        """Create events for multiple comments in a single transaction with last_comment_id update."""
        if not comments:
            return
        await self._conn.execute("BEGIN")
        try:
            for comment in comments:
                await self._conn.execute(
                    "INSERT INTO events (issue_id, event_type, payload) VALUES (?, ?, ?)",
                    (issue_id, "new_comment", json.dumps(comment)),
                )
            max_id = max(c["id"] for c in comments)
            await self._conn.execute(
                "UPDATE issues SET last_comment_id = ?, updated_at = datetime('now') WHERE id = ?",
                (max_id, issue_id),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

    async def get_unprocessed_events(self) -> list[Event]:
        cursor = await self._conn.execute(
            "SELECT * FROM events WHERE processed = 0 ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def mark_event_processed(self, event_id: int):
        await self._conn.execute("UPDATE events SET processed = 1 WHERE id = ?", (event_id,))
        await self._conn.commit()

    # --- Agent Runs ---

    async def create_agent_run(self, issue_id: int, phase: str) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO agent_runs (issue_id, phase) VALUES (?, ?)",
            (issue_id, phase),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def complete_agent_run(self, run_id: int, *, session_id: str | None = None,
                                  result: str = "success", cost_usd: float = 0.0,
                                  input_tokens: int = 0, output_tokens: int = 0,
                                  error_message: str | None = None):
        await self._conn.execute(
            """UPDATE agent_runs SET session_id = ?, completed_at = datetime('now'),
               result = ?, cost_usd = ?, input_tokens = ?, output_tokens = ?, error_message = ?
               WHERE id = ?""",
            (session_id, result, cost_usd, input_tokens, output_tokens, error_message, run_id),
        )
        await self._conn.commit()

    async def get_agent_run(self, run_id: int) -> AgentRun | None:
        cursor = await self._conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        return self._row_to_agent_run(row) if row else None

    async def get_latest_session_for_phase(self, issue_id: int, phase: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT session_id FROM agent_runs WHERE issue_id = ? AND phase = ? AND session_id IS NOT NULL ORDER BY started_at DESC LIMIT 1",
            (issue_id, phase),
        )
        row = await cursor.fetchone()
        return row["session_id"] if row else None

    async def get_daily_spend(self) -> float:
        cursor = await self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM agent_runs WHERE date(started_at) = date('now')"
        )
        row = await cursor.fetchone()
        return row["total"]

    # --- Audit ---

    async def create_audit_entry(self, *, issue_id: int | None, event_id: int | None,
                                  category: str, action: str, detail: str | None,
                                  duration_ms: int | None, success: int,
                                  error_message: str | None) -> None:
        await self._conn.execute(
            """INSERT INTO audit_log
               (issue_id, event_id, category, action, detail, duration_ms, success, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (issue_id, event_id, category, action, detail, duration_ms, success, error_message),
        )
        await self._conn.commit()

    # --- Row mappers ---

    @staticmethod
    def _row_to_issue(row) -> Issue:
        return Issue(
            id=row["id"], repo_owner=row["repo_owner"], repo_name=row["repo_name"],
            issue_number=row["issue_number"], title=row["title"], body=row["body"],
            phase=row["phase"], branch_name=row["branch_name"], pr_number=row["pr_number"],
            workspace_path=row["workspace_path"], plan_approved=bool(row["plan_approved"]),
            plan_commit_hash=row["plan_commit_hash"], last_comment_id=row["last_comment_id"],
            budget_notified=bool(row["budget_notified"]), error_message=row["error_message"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_event(row) -> Event:
        return Event(
            id=row["id"], issue_id=row["issue_id"], event_type=row["event_type"],
            payload=json.loads(row["payload"]), processed=bool(row["processed"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_agent_run(row) -> AgentRun:
        return AgentRun(
            id=row["id"], issue_id=row["issue_id"], phase=row["phase"],
            session_id=row["session_id"], started_at=row["started_at"],
            completed_at=row["completed_at"], result=row["result"],
            cost_usd=row["cost_usd"], input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"], error_message=row["error_message"],
        )
