"""Backlog API v2 の読み書き. 層 4: インフラ.

BacklogApi は本物の API（HTTPS・API キー）、MockBacklog は同じ形の JSON ファイルを読む（試行・テスト用）。
どちらも同じメソッドを持つので、アプリケーション層はどちらを使っているかを知らない。

読み取り（GET）は devtools_common の HttpClient で送り、429 / 5xx / 通信エラーを待ってから再試行する。
書き込み（Wiki の POST / PATCH）は二重に登録・更新しないよう、再試行せず 1 回だけ送る。
200 でも本文が JSON でない（プロキシやメンテナンスの HTML など）・期待した形（一覧・オブジェクト）でない応答は
BacklogResponseError（実行時エラー。CLI の終了コード 3）にする。JSONDecodeError は ValueError の仲間なので、
そのままにすると「設定・引数の誤り（2）」と取り違えるため。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

from devtools_common.httpclient import HttpClient, HttpError, safe_urlopen

from ..config import DEFAULT_TZ, zone
from ..domain.model import Issue, Milestone


class BacklogResponseError(RuntimeError):
    """Backlog の応答が読めない（200 でも JSON でない・形が違う）. 実行時エラー（設定の誤りではない）."""


def _decode(body: bytes, what: str, content_type: str = "") -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        head = body[:80].decode("utf-8", errors="replace").replace("\n", " ").strip()
        kind = f"{content_type}、" if content_type else ""
        raise BacklogResponseError(
            f"Backlog の応答が JSON ではありません（{what}。{kind}先頭: {head!r}）。"
            "space_url・ネットワーク（プロキシ）・Backlog の状態を確かめてください"
        ) from None


def _expect(value: Any, kind: type, what: str) -> Any:
    if not isinstance(value, kind):
        raise BacklogResponseError(f"Backlog の応答の形が違います（{what}。期待: {'一覧' if kind is list else 'オブジェクト'}）")
    return value


class BacklogPort(Protocol):
    def project(self) -> dict[str, Any]: ...
    def versions(self) -> list[dict[str, Any]]: ...
    def issues(self) -> list[dict[str, Any]]: ...
    def find_wiki(self, name: str) -> dict[str, Any] | None: ...
    def save_wiki(self, name: str, content: str) -> dict[str, Any]: ...


class BacklogApi:
    PAGE = 100

    def __init__(
        self,
        space_url: str,
        project_key: str,
        api_key: str,
        timeout: int = 30,
        *,
        opener: Callable[..., Any] = safe_urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base = space_url.rstrip("/") + "/api/v2"
        self.project_key = project_key
        self.api_key = api_key
        self.timeout = timeout
        self._opener = opener
        # Backlog の API は 1 分あたりの回数に上限がある. 429 は Retry-After（無ければ指数バックオフ）だけ待って再試行する
        self._http = HttpClient(self.base, rate_per_sec=2.0, timeout=timeout, opener=opener, sleep=sleep)
        self._project: dict[str, Any] | None = None

    def _call(self, method: str, path: str, params: list[tuple[str, Any]] | None = None, form: dict[str, Any] | None = None) -> Any:
        query = [("apiKey", self.api_key), *(params or [])]
        if method == "GET":
            res = self._http.get(path, params=query)  # type: ignore[arg-type]  # 同じ名前の項目（projectId[]）を並べるため組の列
            ctype = next((v for k, v in res.headers.items() if k.lower() == "content-type"), "")
            return _decode(res.body, f"GET {path}", ctype)
        return self._write(method, path, query, form or {})

    def _write(self, method: str, path: str, query: list[tuple[str, Any]], form: dict[str, Any]) -> Any:
        """POST / PATCH は 1 回だけ送る（失敗しても再試行しない. 届いていれば二重に登録・更新してしまうため）."""
        url = f"{self.base}{path}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method=method)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        try:
            with self._opener(req, timeout=self.timeout) as res:
                body = res.read()
        except urllib.error.HTTPError as e:
            raise HttpError(e.code, f"{method} {path} が失敗しました（再試行していません）: {e.reason or ''}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise HttpError(None, f"{method} {path} が失敗しました（再試行していません）: {getattr(e, 'reason', e)}") from None
        return _decode(body, f"{method} {path}。書き込みは届いている可能性があります")

    def project(self) -> dict[str, Any]:
        if self._project is None:
            self._project = _expect(self._call("GET", f"/projects/{urllib.parse.quote(self.project_key)}"), dict, "プロジェクト")
        return self._project

    def versions(self) -> list[dict[str, Any]]:
        return _expect(self._call("GET", f"/projects/{self.project()['id']}/versions"), list, "マイルストーンの一覧")

    def issues(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = _expect(
                self._call(
                    "GET",
                    "/issues",
                    [
                        ("projectId[]", self.project()["id"]),
                        ("count", self.PAGE),
                        ("offset", offset),
                        ("sort", "created"),
                        ("order", "asc"),
                    ],
                ),
                list,
                "課題の一覧",
            )
            out += page
            if len(page) < self.PAGE:
                return out
            offset += self.PAGE

    def find_wiki(self, name: str) -> dict[str, Any] | None:
        pages = _expect(self._call("GET", "/wikis", [("projectIdOrKey", self.project_key)]), list, "Wiki の一覧")
        return next((p for p in pages if p.get("name") == name), None)

    def save_wiki(self, name: str, content: str) -> dict[str, Any]:
        page = self.find_wiki(name)
        if page:
            return self._call("PATCH", f"/wikis/{page['id']}", form={"content": content, "mailNotify": "false"})
        return self._call(
            "POST", "/wikis", form={"projectId": self.project()["id"], "name": name, "content": content, "mailNotify": "false"}
        )


class MockBacklog:
    """samples/backlog/ の JSON（project.json / versions.json / issues.json）を読む. Wiki の更新は wiki/ に書く."""

    def __init__(self, directory: Path):
        self.dir = directory
        if not (directory / "issues.json").is_file():
            raise FileNotFoundError(f"モックのデータがありません: {directory / 'issues.json'}")

    def _load(self, name: str) -> Any:
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def project(self) -> dict[str, Any]:
        return self._load("project.json")

    def versions(self) -> list[dict[str, Any]]:
        return self._load("versions.json")

    def issues(self) -> list[dict[str, Any]]:
        return self._load("issues.json")

    def find_wiki(self, name: str) -> dict[str, Any] | None:
        p = self._wiki_path(name)
        return {"id": 0, "name": name, "content": p.read_text(encoding="utf-8")} if p.is_file() else None

    def save_wiki(self, name: str, content: str) -> dict[str, Any]:
        p = self._wiki_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        return {"id": 0, "name": name}

    def _wiki_path(self, name: str) -> Path:
        return self.dir / "wiki" / (name.replace("/", "__") + ".md")


def _d(value: str | None) -> date | None:
    """日付の項目（開始日・期限日など. 例: 2026-10-01T00:00:00Z）. 日付だけに意味があるので、そのまま先頭の日付を使う."""
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _ts(value: str | None, tz: str) -> date | None:
    """日時の項目（作成日時・更新日時. UTC）→ タイムゾーン tz での日付."""
    if not value:
        return None
    if "T" not in value:
        return date.fromisoformat(value[:10])
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(zone(tz)).date()


def to_issue(raw: dict[str, Any], keys_by_id: dict[int, str] | None = None, tz: str = DEFAULT_TZ) -> Issue:
    return Issue(
        key=raw["issueKey"],
        summary=raw.get("summary", ""),
        status=(raw.get("status") or {}).get("name", ""),
        assignee=(raw.get("assignee") or {}).get("name", ""),
        issue_type=(raw.get("issueType") or {}).get("name", ""),
        milestones=tuple(m["name"] for m in raw.get("milestone") or []),
        start=_d(raw.get("startDate")),
        due=_d(raw.get("dueDate")),
        estimated_hours=raw.get("estimatedHours"),
        actual_hours=raw.get("actualHours"),
        created=_ts(raw.get("created"), tz) or date.min,
        updated=_ts(raw.get("updated"), tz) or date.min,
        parent_key=(keys_by_id or {}).get(raw.get("parentIssueId") or -1, ""),
    )


def to_milestone(raw: dict[str, Any]) -> Milestone:
    return Milestone(raw["name"], _d(raw.get("startDate")), _d(raw.get("releaseDueDate")), bool(raw.get("archived")))


def load_all(port: BacklogPort, tz: str = DEFAULT_TZ) -> tuple[list[Issue], list[Milestone]]:
    raw = port.issues()
    keys = {r["id"]: r["issueKey"] for r in raw if "id" in r}
    return [to_issue(r, keys, tz) for r in raw], [to_milestone(v) for v in port.versions()]
