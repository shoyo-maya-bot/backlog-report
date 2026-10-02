"""Slack への投稿（Incoming Webhook か Bot トークン）. 層 4: インフラ.

- Webhook: 文章とブロックだけ（画像は送れない）
- Bot トークン（chat:write, files:write）: 投稿と画像のアップロード（files.getUploadURLExternal → completeUploadExternal）
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

API = "https://slack.com/api"
Http = Callable[[urllib.request.Request], dict[str, Any]]


def _default_http(req: urllib.request.Request) -> dict[str, Any]:
    with urllib.request.urlopen(req, timeout=30) as res:  # noqa: S310 — Slack の固定 URL か設定の Webhook
        body = res.read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"ok": body.strip() == "ok", "raw": body}


class SlackError(RuntimeError):
    pass


class SlackClient:
    def __init__(self, *, bot_token: str = "", webhook_url: str = "", http: Http | None = None):
        if not bot_token and not webhook_url:
            raise ValueError("SLACK_BOT_TOKEN か SLACK_WEBHOOK_URL が必要です")
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.http = http or (lambda req: _default_http(req))  # テストで差し替えられるよう、呼ぶときに参照する

    def _api(self, method: str, payload: dict[str, Any] | None = None, form: dict[str, Any] | None = None) -> dict[str, Any]:
        if form is not None:
            req = urllib.request.Request(f"{API}/{method}", data=urllib.parse.urlencode(form).encode(), method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        else:
            req = urllib.request.Request(f"{API}/{method}", data=json.dumps(payload).encode(), method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("Authorization", f"Bearer {self.bot_token}")
        res = self.http(req)
        if not res.get("ok"):
            raise SlackError(f"Slack API {method} が失敗しました: {res.get('error', res)}")
        return res

    def post(self, channel: str, text: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        if self.bot_token:
            return self._api("chat.postMessage", {"channel": channel, "text": text, "blocks": blocks})
        req = urllib.request.Request(self.webhook_url, data=json.dumps({"text": text, "blocks": blocks}).encode(), method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        res = self.http(req)
        if not res.get("ok"):
            raise SlackError(f"Webhook への投稿が失敗しました: {res}")
        return res

    def upload(self, channel: str, path: Path, title: str) -> None:
        if not self.bot_token:
            return  # Webhook では画像を送れない
        data = path.read_bytes()
        got = self._api("files.getUploadURLExternal", form={"filename": path.name, "length": len(data)})
        req = urllib.request.Request(got["upload_url"], data=data, method="POST")
        self.http(req)
        self._api("files.completeUploadExternal", {"files": [{"id": got["file_id"], "title": title}], "channel_id": channel})
