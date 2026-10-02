"""backlog-report のテスト. モックの Backlog（samples/make_samples.py）と、偽の HTTP で Slack を確かめる（外部には接続しない）."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from devtools_common.cli import run_main

from backlog_report.application import report
from backlog_report.config import load_settings
from backlog_report.domain.model import Issue, Milestone
from backlog_report.domain.progress import burndown, summarize
from backlog_report.entrypoints import slack_bot
from backlog_report.entrypoints.cli import main
from backlog_report.infrastructure import snapshots
from backlog_report.infrastructure.backlog import BacklogApi, MockBacklog, load_all
from backlog_report.infrastructure.slack import SlackClient, SlackError

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 2)
_spec = importlib.util.spec_from_file_location("make_samples", ROOT / "samples/make_samples.py")
assert _spec and _spec.loader
make_samples = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_samples)


@pytest.fixture()
def env(tmp_path: Path) -> Path:
    make_samples.generate(tmp_path / "samples/backlog")
    shutil.copy(ROOT / "samples/config.yaml", tmp_path / "samples/config.yaml")
    return tmp_path


def run(*args: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(main, ["--env", "/nonexistent/.env", *args])
    return rc, out.getvalue() + err.getvalue()


def issue(
    key: str,
    status: str = "未対応",
    due: date | None = None,
    start: date | None = None,
    ms: str = "M1",
    est: float | None = 4,
    upd: date = TODAY,
    who: str = "A",
) -> Issue:
    return Issue(key, key, status, who, "タスク", (ms,), start, due, est, 0, date(2026, 9, 1), upd)


# --- ドメイン ----------------------------------------------------------------------------------


def test_summarize_classifies_delay_and_due_soon():
    issues = [
        issue("D", due=date(2026, 10, 1)),  # 期限超過
        issue("S", due=date(2026, 10, 4)),  # 期限間近（3 日以内）
        issue("L", due=date(2026, 10, 20)),
        issue("C", status="完了", due=date(2026, 9, 1)),  # 完了は超過にしない
        issue("N", start=date(2026, 9, 30), due=date(2026, 10, 30)),  # 開始日を過ぎて未着手
        issue("X", due=None, who=""),
    ]
    s = summarize(issues, [Milestone("M1", date(2026, 9, 1), date(2026, 10, 31))], TODAY, ["完了"])
    assert [i.key for i in s.delayed] == ["D"]
    assert [i.key for i in s.due_soon] == ["S"]
    assert [i.key for i in s.not_started] == ["N"]
    assert [i.key for i in s.no_due] == ["X"]
    m = s.milestones[0]
    assert (m.total, m.done, m.delayed) == (6, 1, 1)
    assert m.expected_ratio(TODAY) == pytest.approx(31 / 60)
    names = [a.name for a in s.assignees]
    assert names[0] == "A" and "（未割当）" in names


def test_burndown_uses_snapshots_then_updates():
    ms = Milestone("M1", date(2026, 9, 28), date(2026, 10, 2))
    issues = [issue("a", "完了", upd=date(2026, 9, 29)), issue("b", "完了", upd=date(2026, 10, 1)), issue("c"), issue("d")]
    pts = burndown(issues, ms, date(2026, 10, 1), ["完了"])
    assert [p.ideal for p in pts] == [4.0, 3.0, 2.0, 1.0, 0.0]
    assert [p.actual for p in pts] == [4, 3, 3, 2, None]  # 未来の日は実績なし
    pts = burndown(issues, ms, date(2026, 10, 1), ["完了"], unit="hours", snapshots={date(2026, 9, 30): 10.0})
    assert pts[2].actual == 10.0 and pts[0].ideal == 16.0
    assert burndown(issues, Milestone("M1", None, None), TODAY, ["完了"]) == []


def test_snapshots_overwrite_same_day(tmp_path: Path):
    p = tmp_path / "s.csv"
    snapshots.record(p, TODAY, [("M1", 5, 20.0)])
    snapshots.record(p, TODAY, [("M1", 4, 16.0)])
    snapshots.record(p, date(2026, 10, 3), [("M1", 3, 12.0)])
    assert snapshots.load(p, "M1") == {TODAY: 4.0, date(2026, 10, 3): 3.0}
    assert snapshots.load(p, "M1", "hours")[TODAY] == 16.0


# --- インフラ ----------------------------------------------------------------------------------


def test_mock_backlog_maps_api_shape(env: Path):
    issues, milestones = load_all(MockBacklog(env / "samples/backlog"))
    assert len(issues) == len(make_samples.ISSUES)
    first = issues[0]
    assert (first.key, first.status, first.assignee, first.milestones, first.due) == (
        "SAMPLE-1",
        "完了",
        "担当A",
        ("フェーズ1 設計",),
        date(2026, 9, 7),
    )
    assert [m.archived for m in milestones] == [False, False, True]


def test_backlog_api_pages_and_wiki(monkeypatch: pytest.MonkeyPatch):
    api = BacklogApi("https://example.backlog.com", "SAMPLE", "k")
    calls: list[tuple] = []

    def fake(method, path, params=None, form=None):
        calls.append((method, path, dict(params or []), form))
        if path.startswith("/projects/SAMPLE"):
            return {"id": 9}
        if path == "/issues":
            off = dict(params)["offset"]
            return [{"issueKey": f"S-{off + n}"} for n in range(100 if off == 0 else 3)]
        if path == "/wikis" and method == "GET":
            return [{"id": 77, "name": "進捗"}]
        return {"ok": True}

    monkeypatch.setattr(api, "_call", fake)
    assert len(api.issues()) == 103
    assert [c[2]["offset"] for c in calls if c[1] == "/issues"] == [0, 100]
    api.save_wiki("進捗", "本文")
    assert calls[-1][:2] == ("PATCH", "/wikis/77")
    api.save_wiki("新しいページ", "本文")
    assert calls[-1][:2] == ("POST", "/wikis") and calls[-1][3]["projectId"] == 9


class FakeHttp:
    def __init__(self, fail: str = ""):
        self.requests = []
        self.fail = fail

    def __call__(self, req):
        self.requests.append(req)
        url = req.full_url
        if self.fail and self.fail in url:
            return {"ok": False, "error": "channel_not_found"}
        if url.endswith("files.getUploadURLExternal"):
            return {"ok": True, "upload_url": "https://files.example/upload", "file_id": "F1"}
        return {"ok": True}


def test_slack_bot_token_posts_and_uploads(tmp_path: Path):
    http = FakeHttp()
    c = SlackClient(bot_token="xoxb-test", http=http)
    c.post("C1", "t", [{"type": "section"}])
    png = tmp_path / "a.png"
    png.write_bytes(b"png")
    c.upload("C1", png, "グラフ")
    urls = [r.full_url for r in http.requests]
    assert urls == [
        "https://slack.com/api/chat.postMessage",
        "https://slack.com/api/files.getUploadURLExternal",
        "https://files.example/upload",
        "https://slack.com/api/files.completeUploadExternal",
    ]
    assert http.requests[0].get_header("Authorization") == "Bearer xoxb-test"
    assert json.loads(http.requests[-1].data)["channel_id"] == "C1"
    with pytest.raises(SlackError, match="channel_not_found"):
        SlackClient(bot_token="x", http=FakeHttp(fail="postMessage")).post("C9", "t", [])


def test_slack_webhook_skips_upload(tmp_path: Path):
    http = FakeHttp()
    c = SlackClient(webhook_url="https://hooks.slack.example/T/B/X", http=http)
    c.post("", "t", [])
    c.upload("", tmp_path / "none.png", "x")
    assert [r.full_url for r in http.requests] == ["https://hooks.slack.example/T/B/X"]
    with pytest.raises(ValueError):
        SlackClient()


# --- 設定・入口 --------------------------------------------------------------------------------


def test_config_rejects_secrets(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("backlog: {project_key: X, mock_dir: m, api_key: abc}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="秘密情報"):
        load_settings(p)
    p.write_text("backlog: {project_key: X, space_url: http://insecure}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="https"):
        load_settings(p)


def test_cli_report_online(env: Path):
    rc, out = run("-c", str(env / "samples/config.yaml"), "report", "--milestone", "フェーズ1 設計", "--fail-on-delay")
    assert rc == 1  # 期限超過あり
    assert "| フェーズ1 設計 |" in out and "フェーズ2 製造 |" not in out
    rc, out = run("-c", str(env / "samples/config.yaml"), "report", "--assignee", "担当D")
    assert rc == 0 and "SAMPLE-15" in out and "SAMPLE-8" not in out
    rc, out = run("-c", str(env / "samples/config.yaml"), "report", "--milestone", "存在しない")
    assert rc == 2


def test_cli_batch_files_snapshot_wiki(env: Path):
    rc, out = run("-c", str(env / "samples/config.yaml"), "batch", "--wiki")
    assert rc == 0, out
    files = {p.name for p in (env / "output").iterdir()}
    assert {"report_2026-10-02.md", "gantt.png", "burndown_フェーズ1_設計.png", "burndown_フェーズ2_製造.png"} <= files
    assert not any("フェーズ0" in f for f in files)  # 終了したマイルストーンは出さない
    assert "2026-10-02,フェーズ2 製造,11," in (env / "data/snapshots.csv").read_text(encoding="utf-8")
    wiki = (env / "samples/backlog/wiki/進捗レポート__最新.md").read_text(encoding="utf-8")
    assert "自動で更新" in wiki and "![" not in wiki


def test_cli_batch_slack(env: Path, monkeypatch: pytest.MonkeyPatch):
    http = FakeHttp()
    import backlog_report.infrastructure.slack as slack_mod

    monkeypatch.setattr(slack_mod, "_default_http", http)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    rc, out = run("-c", str(env / "samples/config.yaml"), "batch", "--slack")
    assert rc == 0, out
    posted = json.loads(http.requests[0].data)
    assert posted["channel"] == "C0000000000" and posted["blocks"][0]["type"] == "header"
    assert sum(r.full_url.endswith("completeUploadExternal") for r in http.requests) == 3  # ガント 1 + バーンダウン 2


def test_missing_api_key_is_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BACKLOG_API_KEY", raising=False)
    p = tmp_path / "c.yaml"
    p.write_text("backlog: {project_key: X, space_url: 'https://example.backlog.com'}\n", encoding="utf-8")
    rc, out = run("-c", str(p), "report")
    assert rc == 2 and "BACKLOG_API_KEY" in out


def test_slack_bot_handle(env: Path):
    st = load_settings(env / "samples/config.yaml")
    port = MockBacklog(env / "samples/backlog")
    assert "使い方" in slack_bot.handle("help", port, st)["text"]
    res = slack_bot.handle('"フェーズ2 製造" @担当A', port, st)
    assert res["response_type"] == "in_channel" and "フェーズ2 製造 / 担当A" in res["text"]
    bad = slack_bot.handle("ない", port, st)
    assert bad["response_type"] == "ephemeral" and "マイルストーンがありません" in bad["text"]


def test_build_filters(env: Path):
    st = load_settings(env / "samples/config.yaml")
    r = report.build(MockBacklog(env / "samples/backlog"), st)
    assert [m.name for m in r.milestones] == ["フェーズ1 設計", "フェーズ2 製造"]
    assert r.summary.today == TODAY
