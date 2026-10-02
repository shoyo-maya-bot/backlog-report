"""進捗の計算（純粋な関数）. 層 3: ドメイン."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .model import Issue, Milestone


@dataclass
class MilestoneProgress:
    name: str
    start: date | None
    due: date | None
    total: int
    done: int
    estimated: float
    done_estimated: float
    delayed: int

    @property
    def ratio(self) -> float:
        return self.done / self.total if self.total else 0.0

    @property
    def hours_ratio(self) -> float | None:
        return self.done_estimated / self.estimated if self.estimated else None

    def expected_ratio(self, today: date) -> float | None:
        """期間に対する経過の割合（予定どおりならこの割合が終わっているはず）."""
        if not (self.start and self.due) or self.due <= self.start:
            return None
        return min(1.0, max(0.0, (today - self.start).days / (self.due - self.start).days))


@dataclass
class AssigneeLoad:
    name: str
    open: int = 0
    delayed: int = 0
    remaining_hours: float = 0.0
    due_soon: int = 0


@dataclass
class Summary:
    today: date
    total: int
    done: int
    delayed: list[Issue] = field(default_factory=list)
    due_soon: list[Issue] = field(default_factory=list)
    not_started: list[Issue] = field(default_factory=list)  # 開始日を過ぎても未着手
    no_due: list[Issue] = field(default_factory=list)
    milestones: list[MilestoneProgress] = field(default_factory=list)
    assignees: list[AssigneeLoad] = field(default_factory=list)


def is_done(issue: Issue, done_statuses: list[str]) -> bool:
    return issue.status in done_statuses


def summarize(
    issues: list[Issue],
    milestones: list[Milestone],
    today: date,
    done_statuses: list[str],
    due_soon_days: int = 3,
    first_status: str = "未対応",
) -> Summary:
    s = Summary(today, len(issues), sum(is_done(i, done_statuses) for i in issues))
    for i in issues:
        if is_done(i, done_statuses):
            continue
        if i.due and i.due < today:
            s.delayed.append(i)
        elif i.due and i.due <= today + timedelta(days=due_soon_days):
            s.due_soon.append(i)
        if i.start and i.start < today and i.status == first_status:
            s.not_started.append(i)
        if not i.due:
            s.no_due.append(i)
    s.delayed.sort(key=lambda i: (i.due or today, i.key))
    s.due_soon.sort(key=lambda i: (i.due or today, i.key))

    by_ms: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        for m in i.milestones:
            by_ms[m].append(i)
    for m in milestones:
        if m.archived and m.name not in by_ms:
            continue
        group = by_ms.get(m.name, [])
        done = [i for i in group if is_done(i, done_statuses)]
        s.milestones.append(
            MilestoneProgress(
                m.name,
                m.start,
                m.due,
                len(group),
                len(done),
                sum(i.estimated_hours or 0 for i in group),
                sum(i.estimated_hours or 0 for i in done),
                sum(1 for i in group if i in s.delayed),
            )
        )

    loads: dict[str, AssigneeLoad] = {}
    for i in issues:
        if is_done(i, done_statuses):
            continue
        a = loads.setdefault(i.assignee or "（未割当）", AssigneeLoad(i.assignee or "（未割当）"))
        a.open += 1
        a.delayed += i in s.delayed
        a.due_soon += i in s.due_soon
        a.remaining_hours += max(0.0, (i.estimated_hours or 0) - (i.actual_hours or 0))
    s.assignees = sorted(loads.values(), key=lambda a: (-a.delayed, -a.open, a.name))
    return s


@dataclass
class BurndownPoint:
    day: date
    ideal: float
    actual: float | None


def burndown(
    issues: list[Issue],
    milestone: Milestone,
    today: date,
    done_statuses: list[str],
    *,
    unit: str = "count",
    snapshots: dict[date, float] | None = None,
) -> list[BurndownPoint]:
    """マイルストーンのバーンダウン. 残量は件数（count）か見積時間（hours）.

    実績は、スナップショット（日々の実行で記録した残量）があればそれを、無ければ「完了した課題の最終更新日」を完了日とみなして計算する。
    """
    if not (milestone.start and milestone.due):
        return []
    group = [i for i in issues if milestone.name in i.milestones]

    def weight(i: Issue) -> float:
        return (i.estimated_hours or 0.0) if unit == "hours" else 1.0

    total = sum(weight(i) for i in group)
    days = (milestone.due - milestone.start).days
    points = []
    for n in range(days + 1):
        d = milestone.start + timedelta(days=n)
        ideal = total * (1 - n / days) if days else 0.0
        actual: float | None = None
        if d <= today:
            if snapshots and d in snapshots:
                actual = snapshots[d]
            else:
                actual = total - sum(weight(i) for i in group if is_done(i, done_statuses) and i.updated <= d)
        points.append(BurndownPoint(d, round(ideal, 2), None if actual is None else round(actual, 2)))
    return points
