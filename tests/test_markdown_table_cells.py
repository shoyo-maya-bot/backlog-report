"""Markdown・Wiki の表: マイルストーン名・担当者名・状態名の `|` や改行で列がずれない."""

from __future__ import annotations

from datetime import date

from backlog_report.domain.model import Issue, Milestone
from backlog_report.domain.progress import summarize
from backlog_report.rendering import text

TODAY = date(2026, 10, 2)


def _rows(md: str, header: str) -> list[str]:
    lines = md.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith(header))
    out = []
    for line in lines[i + 2 :]:
        if not line.startswith("|"):
            break
        out.append(line)
    return out


def _cols(row: str) -> int:
    return row.count("|") - 1


def test_pipes_in_names_do_not_break_tables():
    ms = Milestone("v1|α", date(2026, 9, 1), date(2026, 10, 31))
    i = Issue("X-1", "件名", "対応|中", "山田|太郎", "タスク", ("v1|α",), None, date(2026, 10, 1), 4, 0, date(2026, 9, 1), TODAY)
    s = summarize([i], [ms], TODAY, ["完了"])
    md = text.markdown(s, "X")
    milestone_rows = _rows(md, "| マイルストーン |")
    assert milestone_rows and all(_cols(r) == 7 for r in milestone_rows)
    assert "v1／α" in milestone_rows[0]
    delayed = _rows(md, "| 課題 |")
    assert delayed and all(_cols(r) == 6 for r in delayed)
    assert "山田／太郎" in delayed[0] and "対応／中" in delayed[0]
    assignees = _rows(md, "| 担当 |")
    assert assignees and all(_cols(r) == 5 for r in assignees)


def test_newlines_in_cells_become_spaces():
    assert text.cell("a\nb|c\r\nd") == "a b／c d"
