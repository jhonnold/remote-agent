# src/remote_agent/config.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RepoConfig:
    owner: str
    name: str


@dataclass
class PollingConfig:
    interval_seconds: int = 60


@dataclass
class TriggerConfig:
    label: str = "agent"


@dataclass
class WorkspaceConfig:
    base_dir: str = "/home/claude/workspaces"


@dataclass
class DatabaseConfig:
    path: str = "data/agent.db"


@dataclass
class AgentConfig:
    default_model: str = "sonnet"
    planning_model: str = "opus"
    implementation_model: str = "sonnet"
    review_model: str = "sonnet"
    orchestrator_model: str = "haiku"
    max_turns: int = 200
    max_budget_usd: float = 10.0
    daily_budget_usd: float = 50.0


@dataclass
class Config:
    repos: list[RepoConfig]
    users: list[str]
    polling: PollingConfig
    trigger: TriggerConfig
    workspace: WorkspaceConfig
    database: DatabaseConfig
    agent: AgentConfig


def load_config(config_path: str) -> Config:
    """Load and validate configuration from a YAML file."""
    path = Path(config_path).resolve()
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError("Config file is empty")

    # Validate required sections
    for section in ("repos", "users", "polling", "trigger", "workspace", "database", "agent"):
        if section not in raw:
            raise ValueError(f"Missing required config section: {section}")

    repos = [RepoConfig(**r) for r in raw["repos"]]
    if not repos:
        raise ValueError("At least one repo must be configured in repos")

    users = raw["users"]
    if not users:
        raise ValueError("At least one user must be configured in users")

    # Resolve database path relative to config file
    db_path = raw["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(path.parent / db_path)

    return Config(
        repos=repos,
        users=users,
        polling=PollingConfig(**raw.get("polling", {})),
        trigger=TriggerConfig(**raw.get("trigger", {})),
        workspace=WorkspaceConfig(**raw.get("workspace", {})),
        database=DatabaseConfig(path=db_path),
        agent=AgentConfig(**raw.get("agent", {})),
    )
