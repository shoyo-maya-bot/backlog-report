"""設定（config.yaml）と秘密情報（.env）. 層 6: 設定.

config.yaml には秘密情報を書かない。API キー・トークンは .env（コミットしない）か環境変数から読む。

「今日」と課題の更新日時の日付は、タイムゾーン（report.timezone、環境変数 BACKLOG_REPORT_TIMEZONE が優先。
既定 Asia/Tokyo）で決める。定期実行の環境（GitHub Actions など）は UTC で動くため、OS の日付は使わない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

SECRETS = ("BACKLOG_API_KEY", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_WEBHOOK_URL")
DEFAULT_TZ = "Asia/Tokyo"
TZ_ENV = "BACKLOG_REPORT_TIMEZONE"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def zone(name: str) -> ZoneInfo:
    """タイムゾーン名 → ZoneInfo. 分からない名前は ValueError（Windows でデータが無いときは tzdata を入れる）."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"タイムゾーンが分かりません: {name}（例: Asia/Tokyo、UTC。Windows では pip install tzdata）") from None


def local_today(tz: str = DEFAULT_TZ, now: datetime | None = None) -> date:
    """そのタイムゾーンでの今日の日付. now を省略すると現在時刻."""
    now = now or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(zone(tz)).date()


@dataclass
class Settings:
    space_url: str = ""  # 例: https://example.backlog.com
    project_key: str = ""
    mock_dir: str = ""  # 指定すると API の代わりにこのフォルダの JSON を読む（試行・テスト用）
    done_statuses: list[str] = field(default_factory=lambda: ["完了"])
    first_status: str = "未対応"  # 未着手とみなす状態（開始日を過ぎて未着手・ガントの「未着手」の色）
    milestones: list[str] = field(default_factory=list)  # 空ならすべて
    due_soon_days: int = 3
    # 完了率・バーンダウン・担当者別の残り時間の数え方: children（子課題だけ）/ parents（親課題だけ）/ both（両方）
    parent_child: str = "children"
    completion_date: str = "updated"  # 完了日の推定: updated（最終更新日）
    output_dir: str = "output"
    snapshot_file: str = "data/snapshots.csv"
    post_log: str = "data/posted.csv"  # Slack に送ったものの記録（再実行で二重に投稿しないため）
    slack_channel: str = ""
    wiki_page: str = ""  # 例: 進捗レポート/週次。空なら Wiki は更新しない
    today: date | None = None
    timezone: str = DEFAULT_TZ  # 「今日」と更新日時の日付を決めるタイムゾーン
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
        first_status=str(r.get("first_status") or "未対応"),
        milestones=[str(m) for m in r.get("milestones", []) or []],
        due_soon_days=int(r.get("due_soon_days", 3)),
        parent_child=str(r.get("parent_child", "children")),
        completion_date=str(r.get("completion_date", "updated")),
        output_dir=str(r.get("output_dir", "output")),
        snapshot_file=str(r.get("snapshot_file", "data/snapshots.csv")),
        post_log=str(s.get("post_log", "data/posted.csv") or ""),
        slack_channel=str(s.get("channel", "")),
        wiki_page=str(w.get("page", "") or ""),
        timezone=str(os.environ.get(TZ_ENV) or r.get("timezone") or DEFAULT_TZ),
        raw=data,
    )
    zone(st.timezone)
    if r.get("today"):
        st.today = date.fromisoformat(str(r["today"]))
    if st.parent_child not in ("children", "parents", "both"):
        raise ValueError(f"report.parent_child は children / parents / both のどれかです: {st.parent_child}")
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
