"""日々の残量の記録（CSV）. バーンダウンの実績を正確にするため、バッチの実行ごとに 1 行足す. 層 4: インフラ."""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

FIELDS = ["date", "milestone", "remaining_count", "remaining_hours"]


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


def record(path: Path, today: date, rows: list[tuple[str, int, float]]) -> None:
    """同じ日・同じマイルストーンの行は上書きする. 一時ファイルに書いてから置き換える（途中で落ちても記録は消えない）."""
    existing: list[dict[str, str]] = []
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as fh:
            existing = [r for r in csv.DictReader(fh) if not (r["date"] == today.isoformat() and r["milestone"] in {m for m, _, _ in rows})]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(existing)
            for m, cnt, hours in rows:
                w.writerow({"date": today.isoformat(), "milestone": m, "remaining_count": cnt, "remaining_hours": round(hours, 2)})
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
