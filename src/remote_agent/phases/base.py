# src/remote_agent/phases/base.py
from __future__ import annotations
from typing import Protocol

from remote_agent.models import Issue, Event, PhaseResult


class PhaseHandler(Protocol):
    async def handle(self, issue: Issue, event: Event) -> PhaseResult:
        """Handle an event for the given issue and return the next phase."""
        ...
