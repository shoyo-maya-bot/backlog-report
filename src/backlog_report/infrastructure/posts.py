"""Slack へ送ったものの記録（CSV）. 再実行で同じレポートを二重に投稿しないため. 層 4: インフラ.

残量の記録（snapshots.csv）と同じ仕組みで、1 行 1 件の CSV に足していき、一時ファイルに書いてから置き換える。
定期実行の例では、snapshots.csv と同じく report-data ブランチに保存する。
キーは（日付・送り先・プロジェクト・送ったもの）。送ったものは message（本文）か file:<ファイル名>（グラフの画像）。
"""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

FIELDS = ["date", "channel", "project", "item"]
Key = tuple[str, str, str, str]


def key(today: date, channel: str, project: str, item: str) -> Key:
    return (today.isoformat(), channel or "webhook", project, item)


def load(path: Path) -> set[Key]:
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as fh:
        return {(r["date"], r["channel"], r["project"], r["item"]) for r in csv.DictReader(fh)}


def add(path: Path, k: Key) -> None:
    """1 件足す. 一時ファイルに書いてから置き換える（途中で落ちても、それまでの記録は消えない）."""
    rows = sorted(load(path) | {k})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(FIELDS)
            w.writerows(rows)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
