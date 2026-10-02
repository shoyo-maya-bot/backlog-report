"""課題とマイルストーン（Backlog に依存しない形）. 層 3: ドメイン."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Issue:
    key: str
    summary: str
    status: str
    assignee: str
    issue_type: str
    milestones: tuple[str, ...]
    start: date | None
    due: date | None
    estimated_hours: float | None
    actual_hours: float | None
    created: date
    updated: date
    parent_key: str = ""


@dataclass(frozen=True)
class Milestone:
    name: str
    start: date | None
    due: date | None
    archived: bool = False
