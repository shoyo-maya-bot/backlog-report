"""第 7 回: マイルストーン未指定でアーカイブ済みの課題が入る・存在しない担当者で 0/0 を返す."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from devtools_common.cli import run_main

from backlog_report.application import report
from backlog_report.config import load_settings
from backlog_report.entrypoints import slack_bot
from backlog_report.entrypoints.cli import main
from backlog_report.infrastructure.backlog import MockBacklog

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 2)
ARCHIVED = "フェーズ0 準備（終了）"


@pytest.fixture()
def env(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "samples/backlog", tmp_path / "samples/backlog", ignore=shutil.ignore_patterns("wiki"))
    shutil.copy(ROOT / "samples/config.yaml", tmp_path / "samples/config.yaml")
    issues_path = tmp_path / "samples/backlog/issues.json"
    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    base = issues[0]

    def add(key: str, milestones: list[str], who: str = "担当Z") -> None:
        i = copy.deepcopy(base)
        i.update(
            id=90000 + len(issues),
            issueKey=key,
            summary=f"{key} の課題",
            status={"id": 1, "name": "未対応"},
            assignee={"name": who},
            dueDate="2026-08-20T00:00:00Z",  # 期限超過
            milestone=[{"id": 3 if m == ARCHIVED else 2, "name": m} for m in milestones],
        )
        issues.append(i)

    add("SAMPLE-90", [ARCHIVED])  # アーカイブ済みのマイルストーンだけ
    add("SAMPLE-91", [ARCHIVED, "フェーズ2 製造"], who="担当A")  # アーカイブ済みと進行中の両方
    add("SAMPLE-92", [], who="担当A")  # マイルストーンなし
    issues_path.write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def build(env: Path, **kw):
    st = load_settings(env / "samples/config.yaml")
    return report.build(MockBacklog(env / "samples/backlog"), st, today=TODAY, **kw)


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(main, ["--env", "/nonexistent/.env", *args])
    return rc, out.getvalue(), err.getvalue()


def test_archived_only_issues_are_excluded_when_no_milestone_is_given(env: Path):
    r = build(env)
    keys = {i.key for i in r.issues}
    assert "SAMPLE-90" not in keys  # 以前は期限超過・担当者別・全体の完了率に入っていた
    assert "SAMPLE-91" in keys  # 進行中のマイルストーンにもある課題は入れる
    assert "SAMPLE-90" not in {i.key for i in r.summary.delayed}
    assert "担当Z" not in {a.name for a in r.summary.assignees}
    assert ARCHIVED not in {m.name for m in r.milestones}


def test_issues_without_milestone_are_still_included(env: Path):
    r = build(env)
    all_issues = json.loads((env / "samples/backlog/issues.json").read_text(encoding="utf-8"))
    no_ms = {i["issueKey"] for i in all_issues if not i["milestone"]}
    assert no_ms and no_ms <= {i.key for i in r.issues}


def test_archived_milestone_when_given_explicitly(env: Path):
    r = build(env, milestone=ARCHIVED)
    assert {"SAMPLE-90", "SAMPLE-91"} <= {i.key for i in r.issues}


def test_unknown_assignee_is_an_error(env: Path):
    with pytest.raises(ValueError, match="担当者がいません: 担当X") as e:
        build(env, assignee="担当X")  # 以前は 0 件中 0 件のレポート
    assert "担当A" in str(e.value)
    rc, _, err = run("-c", str(env / "samples/config.yaml"), "report", "--assignee", "担当X")
    assert rc == 2 and "担当者がいません" in err
    res = slack_bot.handle("@担当X", MockBacklog(env / "samples/backlog"), load_settings(env / "samples/config.yaml"))
    assert res["response_type"] == "ephemeral" and "担当者がいません" in res["text"]


def test_known_assignee_without_issues_in_target_warns(env: Path):
    r = build(env, assignee="担当Z")  # 担当Z の課題はアーカイブ済みのマイルストーンにだけある
    assert r.summary.total == 0
    assert any("担当Z" in w for w in r.warnings)
    rc, _, err = run("-c", str(env / "samples/config.yaml"), "report", "--assignee", "担当Z")
    assert rc == 0 and "警告: 担当者 担当Z の課題は" in err
