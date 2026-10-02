"""ガントチャートとバーンダウン（matplotlib・PNG）. 層 5: 表示."""

from __future__ import annotations

import warnings
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from ..domain.model import Issue  # noqa: E402
from ..domain.progress import BurndownPoint  # noqa: E402

JP_FONTS = ["Yu Gothic", "Meiryo", "Hiragino Sans", "Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "IPAGothic"]
COLORS = {"done": "#9ca3af", "delayed": "#dc2626", "doing": "#2563eb", "todo": "#93c5fd"}


def _font() -> None:
    names = {f.name for f in font_manager.fontManager.ttflist}
    for n in JP_FONTS:
        if n in names:
            plt.rcParams["font.family"] = n
            return


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fig.tight_layout()
        fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def gantt(issues: list[Issue], today: date, done_statuses: list[str], path: Path, title: str, limit: int = 40) -> Path | None:
    rows = [i for i in issues if i.due][:limit]
    if not rows:
        return None
    _font()
    rows.sort(key=lambda i: (i.start or i.due, i.due, i.key))
    fig, ax = plt.subplots(figsize=(11, max(2.5, 0.32 * len(rows) + 1.2)))
    for n, i in enumerate(rows):
        start = i.start or i.due
        end = i.due + timedelta(days=1)
        if i.status in done_statuses:
            color = COLORS["done"]
        elif i.due < today:
            color = COLORS["delayed"]
        elif i.status != "未対応":
            color = COLORS["doing"]
        else:
            color = COLORS["todo"]
        ax.barh(n, (end - start).days, left=mdates.date2num(start), color=color, height=0.6)
    ax.set_yticks(range(len(rows)), [f"{i.key} {i.summary[:18]}" for i in rows], fontsize=8)
    ax.invert_yaxis()
    ax.axvline(mdates.date2num(today), color="#111827", linestyle="--", linewidth=1)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.set_title(title)
    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(color=c, label=lb)
            for lb, c in (
                ("完了", COLORS["done"]),
                ("期限超過", COLORS["delayed"]),
                ("対応中", COLORS["doing"]),
                ("未着手", COLORS["todo"]),
            )
        ],
        fontsize=8,
        loc="upper left",
        bbox_to_anchor=(1.01, 1),
    )
    return _save(fig, path)


def burndown(points: list[BurndownPoint], path: Path, title: str, unit_label: str) -> Path | None:
    if not points:
        return None
    _font()
    fig, ax = plt.subplots(figsize=(8, 4))
    days = [mdates.date2num(p.day) for p in points]
    ax.plot(days, [p.ideal for p in points], linestyle="--", color="#9ca3af", label="理想")
    actual = [(d, p.actual) for d, p in zip(days, points, strict=True) if p.actual is not None]
    if actual:
        ax.plot([d for d, _ in actual], [v for _, v in actual], marker="o", markersize=3, color="#2563eb", label="実績（残り）")
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.set_ylabel(unit_label)
    ax.set_ylim(bottom=0)
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    return _save(fig, path)
