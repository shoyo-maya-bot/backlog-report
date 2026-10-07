"""Slack ボット（Socket Mode）. `/progress [マイルストーン] [@担当者名]` でその場のレポートを返す. 層 1: 入口.

slack_bolt は任意の依存（pip install -e ".[bot]"）。応答を組み立てる handle() は slack_bolt なしでテストできる。
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from typing import Any

from devtools_common.httpclient import HttpError

from ..application import report
from ..config import Settings, secret
from ..infrastructure.backlog import BacklogPort
from ..rendering import text

HELP = "使い方: `/progress`（全体）／ `/progress マイルストーン名`／ `/progress @担当者名`／ `/progress help`"


def _error(message: str) -> dict[str, Any]:
    return {"response_type": "ephemeral", "text": message}


def handle(command_text: str, port: BacklogPort, st: Settings) -> dict[str, Any]:
    """コマンドの文字列 → Slack への応答. 入力の誤りや Backlog の失敗も、利用者に分かる文で返す（黙って失敗しない）."""
    try:
        args = shlex.split(command_text or "")
    except ValueError:
        return _error(f'引用符（"）が閉じていません: `{command_text}`\n{HELP}')
    if args and args[0] in ("help", "ヘルプ", "-h"):
        return {"response_type": "ephemeral", "text": HELP}
    milestone = next((a for a in args if not a.startswith("@")), "")
    assignee = next((a[1:] for a in args if a.startswith("@")), "")
    try:
        r = report.build(port, st, milestone=milestone, assignee=assignee)
    except ValueError as e:
        return _error(f"{e}\n{HELP}")
    except HttpError as e:
        return _error(f"Backlog から課題を読み取れませんでした（{e}）。しばらくしてからもう一度試してください")
    msg, blocks = text.slack_blocks(
        r.summary, r.project + (f" / {milestone}" if milestone else "") + (f" / {assignee}" if assignee else ""), st.space_url
    )
    return {"response_type": "in_channel", "text": msg, "blocks": blocks}


def run(port_factory: Callable[[], BacklogPort], st: Settings, command: str = "/progress") -> None:
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError as e:  # pragma: no cover — 任意の依存
        raise SystemExit('slack_bolt が必要です: pip install -e ".[bot]"') from e
    app = App(token=secret("SLACK_BOT_TOKEN"))

    @app.command(command)
    def _progress(ack, command, respond):  # pragma: no cover — Slack との結合部
        ack()
        respond(handle(command.get("text", ""), port_factory(), st))

    SocketModeHandler(app, secret("SLACK_APP_TOKEN")).start()  # pragma: no cover
