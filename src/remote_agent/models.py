# src/remote_agent/models.py
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Issue:
    id: int
    repo_owner: str
    repo_name: str
    issue_number: int
    title: str
    body: str | None
    phase: str
    branch_name: str | None = None
    pr_number: int | None = None
    workspace_path: str | None = None
    design_approved: bool = False
    design_commit_hash: str | None = None
    plan_path: str | None = None
    last_comment_id: int = 0
    last_review_id: int = 0
    issue_closed_seen: bool = False
    last_issue_comment_id: int = 0
    budget_notified: bool = False
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class Event:
    id: int
    issue_id: int
    event_type: str
    payload: dict = field(default_factory=dict)
    processed: bool = False
    created_at: str | None = None


@dataclass
class AgentRun:
    id: int
    issue_id: int
    phase: str
    session_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    result: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error_message: str | None = None


@dataclass
class PhaseResult:
    next_phase: str
    error_message: str | None = None
