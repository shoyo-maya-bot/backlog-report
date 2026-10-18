"""Slack ボット（Socket Mode）. `/progress [マイルストーン] [@担当者名] [--channel]` でその場のレポートを返す. 層 1: 入口.

応答は既定で本人だけに見える（ephemeral）。`--channel` を付けたときだけチャンネルに出す（in_channel）。
slack.allowed_users / slack.allowed_channels を設定すると、そこに無い利用者・チャンネルからのコマンドには課題を読まずに断る
（未設定なら制限しない。起動時に警告する）。

slack_bolt は任意の依存（pip install -e ".[bot]"）。応答を組み立てる handle() は slack_bolt なしでテストできる。
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Callable
from typing import Any

from devtools_common.httpclient import HttpError

from ..application import report
from ..config import Settings, secret
from ..infrastructure.backlog import BacklogPort, BacklogResponseError
from ..rendering import text

log = logging.getLogger(__name__)

CHANNEL_FLAG = "--channel"
DENIED = "このコマンドを使う権限がありません（この利用者・チャンネルは slack.allowed_users / slack.allowed_channels にありません）"
HELP = (
    "使い方: `/progress`（全体）／ `/progress マイルストーン名`／ `/progress @担当者名`／ `/progress help`\n"
    f"応答はあなただけに見えます。チャンネルに出すときは `{CHANNEL_FLAG}` を付けます（例: `/progress マイルストーン名 {CHANNEL_FLAG}`）"
)


def _error(message: str) -> dict[str, Any]:
    return {"response_type": "ephemeral", "text": message}


def allowed(st: Settings, user_id: str = "", channel_id: str = "") -> bool:
    """利用者・チャンネルが許可されているか. 許可の一覧が空の項目は制限しない（両方空なら誰でも使える）."""
    if st.bot_allowed_users and user_id not in st.bot_allowed_users:
        return False
    return not (st.bot_allowed_channels and channel_id not in st.bot_allowed_channels)


def handle(command_text: str, port: BacklogPort, st: Settings, *, user_id: str = "", channel_id: str = "") -> dict[str, Any]:
    """コマンドの文字列 → Slack への応答. 入力の誤りや Backlog の失敗も、利用者に分かる文で返す（黙って失敗しない）.

    許可の一覧（slack.allowed_users / allowed_channels）があるときは、無い利用者・チャンネルには課題を読まずに断る.
    """
    if not allowed(st, user_id, channel_id):
        log.warning("/progress を断りました: user=%s channel=%s", user_id or "?", channel_id or "?")
        return _error(DENIED)
    try:
        args = shlex.split(command_text or "")
    except ValueError:
        return _error(f'引用符（"）が閉じていません: `{text.mrkdwn_escape(command_text)}`\n{HELP}')
    if args and args[0] in ("help", "ヘルプ", "-h"):
        return {"response_type": "ephemeral", "text": HELP}
    public = CHANNEL_FLAG in args
    args = [a for a in args if a != CHANNEL_FLAG]
    unknown = [a for a in args if a.startswith("--")]
    if unknown:
        return _error(f"知らないオプションです: `{text.mrkdwn_escape(unknown[0])}`\n{HELP}")
    milestone = next((a for a in args if not a.startswith("@")), "")
    assignee = next((a[1:] for a in args if a.startswith("@")), "")
    try:
        r = report.build(port, st, milestone=milestone, assignee=assignee)
    except ValueError as e:
        return _error(f"{text.mrkdwn_escape(str(e))}\n{HELP}")
    except HttpError as e:
        return _error(f"Backlog から課題を読み取れませんでした（{text.mrkdwn_escape(str(e))}）。しばらくしてからもう一度試してください")
    except BacklogResponseError as e:  # 200 でも JSON でない応答など（入力の誤りではない）
        return _error(f"Backlog の応答を読めませんでした（{text.mrkdwn_escape(str(e))}）。しばらくしてからもう一度試してください")
    msg, blocks = text.slack_blocks(
        r.summary, r.project + (f" / {milestone}" if milestone else "") + (f" / {assignee}" if assignee else ""), st.space_url
    )
    return {"response_type": "in_channel" if public else "ephemeral", "text": msg, "blocks": blocks}


def run(port_factory: Callable[[], BacklogPort], st: Settings, command: str = "/progress") -> None:
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError as e:  # pragma: no cover — 任意の依存
        raise SystemExit('slack_bolt が必要です: pip install -e ".[bot]"') from e
    if not st.bot_allowed_users and not st.bot_allowed_channels:
        log.warning(
            "slack.allowed_users / slack.allowed_channels が未設定なので、ワークスペースの誰でも /progress で課題を見られます"
            "（制限するには config.yaml に設定してください）"
        )
    app = App(token=secret("SLACK_BOT_TOKEN"))

    @app.command(command)
    def _progress(ack, command, respond):  # pragma: no cover — Slack との結合部
        ack()
        user, channel = command.get("user_id", ""), command.get("channel_id", "")
        respond(handle(command.get("text", ""), port_factory(), st, user_id=user, channel_id=channel))

    SocketModeHandler(app, secret("SLACK_APP_TOKEN")).start()  # pragma: no cover
