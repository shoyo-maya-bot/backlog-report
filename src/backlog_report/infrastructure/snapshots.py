"""日々の残量の記録（CSV）. バーンダウンの実績を正確にするため、バッチの実行ごとに 1 行足す. 層 4: インフラ.

日付は `2026-10-01`（ISO）のほか、Excel で開いて保存したときの `2026/10/01`・`2026/10/1` も読む。
読めない行（日付・残量が壊れている）は、ファイル名と行番号を付けて警告し、その行だけ飛ばす（記録とレポートは続ける）。
"""

from __future__ import annotations

import csv
import os
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

FIELDS = ["date", "milestone", "remaining_count", "remaining_hours", "unit"]
UNITS = ("count", "hours")

_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def parse_date(value: str) -> date:
    """記録の日付. YYYY-MM-DD・YYYY/MM/DD（月日は 1 桁も可）. 読めなければ ValueError."""
    m = _DATE_RE.fullmatch((value or "").strip())
    if not m:
        raise ValueError(f"日付が読めません: {value!r}")
    return date(int(m[1]), int(m[2]), int(m[3]))


def _rows(path: Path, milestone: str, warnings: list[str] | None) -> Iterator[tuple[date, dict[str, str], int]]:
    """そのマイルストーンの行を（日付, 行, 行番号）で返す. 日付の読めない行は警告して飛ばす."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("milestone") != milestone:
                continue
            try:
                d = parse_date(row.get("date") or "")
            except ValueError as e:
                _warn(warnings, path, reader.line_num, str(e))
                continue
            yield d, row, reader.line_num


def _warn(warnings: list[str] | None, path: Path, line: int, reason: str) -> None:
    msg = f"残量の記録 {path.name} の {line} 行目を飛ばしました（{reason}）"
    if warnings is not None and msg not in warnings:
        warnings.append(msg)


def load(path: Path, milestone: str, unit: str = "count", warnings: list[str] | None = None) -> dict[date, float]:
    """日付 → 残量. 読めない行は warnings に（ファイル名・行番号付きで）足して飛ばす."""
    if not path.is_file():
        return {}
    out: dict[date, float] = {}
    col = "remaining_hours" if unit == "hours" else "remaining_count"
    for d, row, line in _rows(path, milestone, warnings):
        value = (row.get(col) or "").strip()
        if value == "":
            continue
        try:
            out[d] = float(value)
        except ValueError:
            _warn(warnings, path, line, f"{col} が数値ではありません: {value!r}")
    return out


def _row_unit(row: dict[str, str]) -> str:
    """その日のバーンダウンの単位（count / hours）. 単位の列が無い古い記録は残量から推定し、どちらとも言えなければ空."""
    unit = (row.get("unit") or "").strip()
    if unit in UNITS:
        return unit
    try:
        count, hours = float(row.get("remaining_count") or 0), float(row.get("remaining_hours") or 0)
    except ValueError:
        return ""
    if hours > 0:
        return "hours"
    return "count" if count > 0 else ""  # 残りが 0 件・0 時間の日はどちらの単位でも同じ


def load_units(path: Path, milestone: str, warnings: list[str] | None = None) -> dict[date, str]:
    """日付 → その日の記録の単位（count / hours。分からない日は空）. 読めない行は警告して飛ばす."""
    if not path.is_file():
        return {}
    return {d: _row_unit(row) for d, row, _ in _rows(path, milestone, warnings)}


def _same_day(value: str, today: date) -> bool:
    try:
        return parse_date(value) == today
    except ValueError:
        return False  # 読めない日付の行はそのまま残す（消さない）


def record(path: Path, today: date, rows: list[tuple[str, int, float] | tuple[str, int, float, str]]) -> None:
    """同じ日・同じマイルストーンの行は上書きする. 一時ファイルに書いてから置き換える（途中で落ちても記録は消えない）.

    rows: (マイルストーン, 残り件数, 残り見積時間[, 単位]). 単位はその日のバーンダウンの単位（count / hours）。
    単位の列が無い古い記録はそのまま残す（読むときに残量から推定する）。
    """
    existing: list[dict[str, str]] = []
    names = {row[0] for row in rows}
    if path.is_file():
        with path.open(encoding="utf-8-sig", newline="") as fh:
            existing = [r for r in csv.DictReader(fh) if not (r.get("milestone") in names and _same_day(r.get("date") or "", today))]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(existing)
            for m, cnt, hours, *rest in rows:
                unit = rest[0] if rest else ""
                w.writerow(
                    {"date": today.isoformat(), "milestone": m, "remaining_count": cnt, "remaining_hours": round(hours, 2), "unit": unit}
                )
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
