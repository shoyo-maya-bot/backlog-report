"""Backlog API v2 の読み書き. 層 4: インフラ.

BacklogApi は本物の API（HTTPS・API キー）、MockBacklog は同じ形の JSON ファイルを読む（試行・テスト用）。
どちらも同じメソッドを持つので、アプリケーション層はどちらを使っているかを知らない。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from ..domain.model import Issue, Milestone


class BacklogPort(Protocol):
    def project(self) -> dict[str, Any]: ...
    def versions(self) -> list[dict[str, Any]]: ...
    def issues(self) -> list[dict[str, Any]]: ...
    def find_wiki(self, name: str) -> dict[str, Any] | None: ...
    def save_wiki(self, name: str, content: str) -> dict[str, Any]: ...


class BacklogApi:
    PAGE = 100

    def __init__(self, space_url: str, project_key: str, api_key: str, timeout: int = 30):
        self.base = space_url.rstrip("/") + "/api/v2"
        self.project_key = project_key
        self.api_key = api_key
        self.timeout = timeout
        self._project: dict[str, Any] | None = None

    def _call(self, method: str, path: str, params: list[tuple[str, Any]] | None = None, form: dict[str, Any] | None = None) -> Any:
        query = urllib.parse.urlencode([("apiKey", self.api_key), *(params or [])])
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        req = urllib.request.Request(f"{self.base}{path}?{query}", data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=self.timeout) as res:  # noqa: S310 — https の設定値のみ
            return json.loads(res.read().decode("utf-8"))

    def project(self) -> dict[str, Any]:
        if self._project is None:
            self._project = self._call("GET", f"/projects/{urllib.parse.quote(self.project_key)}")
        return self._project

    def versions(self) -> list[dict[str, Any]]:
        return self._call("GET", f"/projects/{self.project()['id']}/versions")

    def issues(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._call(
                "GET",
                "/issues",
                [("projectId[]", self.project()["id"]), ("count", self.PAGE), ("offset", offset), ("sort", "created"), ("order", "asc")],
            )
            out += page
            if len(page) < self.PAGE:
                return out
            offset += self.PAGE

    def find_wiki(self, name: str) -> dict[str, Any] | None:
        pages = self._call("GET", "/wikis", [("projectIdOrKey", self.project_key)])
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
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date() if "T" in value else date.fromisoformat(value[:10])


def to_issue(raw: dict[str, Any], keys_by_id: dict[int, str] | None = None) -> Issue:
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
        created=_d(raw.get("created")) or date.min,
        updated=_d(raw.get("updated")) or date.min,
        parent_key=(keys_by_id or {}).get(raw.get("parentIssueId") or -1, ""),
    )


def to_milestone(raw: dict[str, Any]) -> Milestone:
    return Milestone(raw["name"], _d(raw.get("startDate")), _d(raw.get("releaseDueDate")), bool(raw.get("archived")))


def load_all(port: BacklogPort) -> tuple[list[Issue], list[Milestone]]:
    raw = port.issues()
    keys = {r["id"]: r["issueKey"] for r in raw if "id" in r}
    return [to_issue(r, keys) for r in raw], [to_milestone(v) for v in port.versions()]
