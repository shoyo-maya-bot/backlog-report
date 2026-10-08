"""日々の残量の記録（CSV）. バーンダウンの実績を正確にするため、バッチの実行ごとに 1 行足す. 層 4: インフラ."""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

FIELDS = ["date", "milestone", "remaining_count", "remaining_hours", "unit"]
UNITS = ("count", "hours")


def load(path: Path, milestone: str, unit: str = "count") -> dict[date, float]:
    if not path.is_file():
        return {}
    out: dict[date, float] = {}
    col = "remaining_hours" if unit == "hours" else "remaining_count"
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["milestone"] == milestone and row[col] != "":
                out[date.fromisoformat(row["date"])] = float(row[col])
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


def load_units(path: Path, milestone: str) -> dict[date, str]:
    """日付 → その日の記録の単位（count / hours。分からない日は空）."""
    if not path.is_file():
        return {}
    out: dict[date, str] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["milestone"] == milestone:
                out[date.fromisoformat(row["date"])] = _row_unit(row)
    return out


def record(path: Path, today: date, rows: list[tuple[str, int, float] | tuple[str, int, float, str]]) -> None:
    """同じ日・同じマイルストーンの行は上書きする. 一時ファイルに書いてから置き換える（途中で落ちても記録は消えない）.

    rows: (マイルストーン, 残り件数, 残り見積時間[, 単位]). 単位はその日のバーンダウンの単位（count / hours）。
    単位の列が無い古い記録はそのまま残す（読むときに残量から推定する）。
    """
    existing: list[dict[str, str]] = []
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as fh:
            existing = [
                r for r in csv.DictReader(fh) if not (r["date"] == today.isoformat() and r["milestone"] in {row[0] for row in rows})
            ]
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
