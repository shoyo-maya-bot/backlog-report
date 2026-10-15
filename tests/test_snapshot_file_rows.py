"""残量の記録（snapshots.csv）の読み込み: Excel 形式の日付を読み、読めない行は場所付きで警告して飛ばす."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from backlog_report.infrastructure import snapshots

HEADER = "date,milestone,remaining_count,remaining_hours,unit\n"


def write(p: Path, *rows: str) -> Path:
    p.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return p


def test_reads_slash_dates_saved_by_excel(tmp_path: Path):
    p = write(tmp_path / "s.csv", "2026/10/01,M1,5,20,count", "2026/9/30,M1,6,24,count", "2026-10-02,M1,4,16,count")
    assert snapshots.load(p, "M1") == {date(2026, 9, 30): 6.0, date(2026, 10, 1): 5.0, date(2026, 10, 2): 4.0}
    assert snapshots.load_units(p, "M1")[date(2026, 10, 1)] == "count"


def test_bad_rows_are_skipped_with_file_and_line(tmp_path: Path):
    p = write(
        tmp_path / "s.csv", "2026-10-01,M1,5,20,count", "1日目,M1,4,16,count", "2026-10-03,M1,abc,12,count", "2026-13-01,M1,1,1,count"
    )
    warnings: list[str] = []
    assert snapshots.load(p, "M1", warnings=warnings) == {date(2026, 10, 1): 5.0}
    assert len(warnings) == 3
    assert "s.csv の 3 行目" in warnings[0] and "'1日目'" in warnings[0]
    assert "s.csv の 4 行目" in warnings[1] and "remaining_count" in warnings[1]
    assert "s.csv の 5 行目" in warnings[2]
    units_warnings: list[str] = []
    assert set(snapshots.load_units(p, "M1", warnings=units_warnings)) == {date(2026, 10, 1), date(2026, 10, 3)}
    assert len(units_warnings) == 2


def test_record_continues_and_keeps_unreadable_rows(tmp_path: Path):
    p = write(tmp_path / "s.csv", "2026/10/02,M1,9,9,count", "壊れた日付,M1,1,1,count")
    snapshots.record(p, date(2026, 10, 2), [("M1", 4, 16.0, "count")])
    text = p.read_text(encoding="utf-8")
    assert "2026/10/02" not in text  # Excel 形式の同じ日の行も上書きする
    assert "2026-10-02,M1,4,16.0,count" in text
    assert "壊れた日付,M1,1,1,count" in text  # 読めない行は消さない
    assert snapshots.load(p, "M1") == {date(2026, 10, 2): 4.0}
