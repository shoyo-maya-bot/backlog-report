"""設定（config.yaml）と秘密情報（.env）. 層 6: 設定.

config.yaml には秘密情報を書かない。API キー・トークンは .env（コミットしない）か環境変数から読む。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

SECRETS = ("BACKLOG_API_KEY", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_WEBHOOK_URL")


@dataclass
class Settings:
    space_url: str = ""  # 例: https://example.backlog.com
    project_key: str = ""
    mock_dir: str = ""  # 指定すると API の代わりにこのフォルダの JSON を読む（試行・テスト用）
    done_statuses: list[str] = field(default_factory=lambda: ["完了"])
    milestones: list[str] = field(default_factory=list)  # 空ならすべて
    due_soon_days: int = 3
    completion_date: str = "updated"  # 完了日の推定: updated（最終更新日）
    output_dir: str = "output"
    snapshot_file: str = "data/snapshots.csv"
    slack_channel: str = ""
    wiki_page: str = ""  # 例: 進捗レポート/週次。空なら Wiki は更新しない
    today: date | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def load_settings(path: Path) -> Settings:
    if not path.is_file():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}（config.example.yaml をコピーしてください）")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    b, r, s, w = data.get("backlog", {}), data.get("report", {}), data.get("slack", {}), data.get("wiki", {})
    leaked = [
        f"{sec}.{k}"
        for sec, d in (("backlog", b), ("slack", s))
        for k in d
        if k.lower() in ("api_key", "apikey", "token", "bot_token", "app_token", "webhook_url")
    ]
    if leaked:
        raise ValueError(f"config.yaml に秘密情報の項目があります: {', '.join(leaked)}。.env に書いてください")
    st = Settings(
        space_url=str(b.get("space_url", "")).rstrip("/"),
        project_key=str(b.get("project_key", "")),
        mock_dir=str(b.get("mock_dir", "") or ""),
        done_statuses=list(r.get("done_statuses", ["完了"])),
        milestones=[str(m) for m in r.get("milestones", []) or []],
        due_soon_days=int(r.get("due_soon_days", 3)),
        completion_date=str(r.get("completion_date", "updated")),
        output_dir=str(r.get("output_dir", "output")),
        snapshot_file=str(r.get("snapshot_file", "data/snapshots.csv")),
        slack_channel=str(s.get("channel", "")),
        wiki_page=str(w.get("page", "") or ""),
        raw=data,
    )
    if r.get("today"):
        st.today = date.fromisoformat(str(r["today"]))
    if not st.project_key:
        raise ValueError("backlog.project_key が必要です")
    if not st.mock_dir and not st.space_url.startswith("https://"):
        raise ValueError("backlog.space_url（https://...）か backlog.mock_dir が必要です")
    return st


def secret(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or value.startswith("<your-"):
        raise ValueError(f"環境変数 {name} が設定されていません（.env.example を参照）")
    return value
