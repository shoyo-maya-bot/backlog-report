"""採点で見つかった不具合の再現テスト（タイムゾーン・記録の上書き・ボットの例外・定期実行の例）. 名前はすべて架空."""

from __future__ import annotations

import contextlib
import csv
import io
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml
from devtools_common.cli import run_main
from devtools_common.httpclient import HttpError

from backlog_report.application import report
from backlog_report.config import load_settings, local_today
from backlog_report.entrypoints import slack_bot
from backlog_report.entrypoints.cli import main
from backlog_report.infrastructure import snapshots
from backlog_report.infrastructure.backlog import MockBacklog, to_issue

ROOT = Path(__file__).resolve().parent.parent
# 定期実行の例と同じ時刻（UTC 23:50 = 日本時間の翌日 8:50）
UTC_2350 = datetime(2026, 10, 1, 23, 50, tzinfo=UTC)


class OneIssue:
    """期限が 2026-10-01（日本時間の前日）の課題が 1 つだけある Backlog."""

    def project(self):
        return {"id": 1, "projectKey": "X"}

    def versions(self):
        return [{"name": "M1", "startDate": "2026-09-01T00:00:00Z", "releaseDueDate": "2026-10-31T00:00:00Z"}]

    def issues(self):
        return [
            {
                "id": 1,
                "issueKey": "X-1",
                "summary": "期限切れ",
                "status": {"name": "未対応"},
                "milestone": [{"name": "M1"}],
                "dueDate": "2026-10-01T00:00:00Z",
                "created": "2026-09-01T00:00:00Z",
                "updated": "2026-10-01T23:30:00Z",
            }
        ]

    def find_wiki(self, name):
        return None

    def save_wiki(self, name, content):
        return {}


def _settings(tmp_path: Path, extra: str = ""):
    p = tmp_path / "config.yaml"
    p.write_text(
        f"backlog: {{project_key: X, mock_dir: m}}\nreport: {{snapshot_file: {tmp_path.as_posix()}/data/s.csv{extra}}}\n", encoding="utf-8"
    )
    return load_settings(p)


# --- 高: 今日の日付をタイムゾーンで決める -----------------------------------------------------------


def test_local_today_crosses_utc_midnight():
    assert local_today("Asia/Tokyo", UTC_2350) == date(2026, 10, 2)
    assert local_today("UTC", UTC_2350) == date(2026, 10, 1)


def test_build_uses_timezone_for_today_and_delay(tmp_path: Path):
    st = _settings(tmp_path)
    assert st.timezone == "Asia/Tokyo"  # 既定
    r = report.build(OneIssue(), st, now=UTC_2350)
    assert r.summary.today == date(2026, 10, 2)
    assert [i.key for i in r.summary.delayed] == ["X-1"]  # 前日が期限の課題は期限超過
    # 更新日時も日本時間の日付にする（UTC 23:30 → 翌日）
    assert r.issues[0].updated == date(2026, 10, 2)
    # 記録も日本時間の日付で残る
    report.record_snapshot(r, st)
    assert list(snapshots.load(Path(st.snapshot_file), "M1")) == [date(2026, 10, 2)]


def test_timezone_from_config_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    st = _settings(tmp_path, ", timezone: UTC")
    assert st.timezone == "UTC"
    assert report.build(OneIssue(), st, now=UTC_2350).summary.today == date(2026, 10, 1)
    monkeypatch.setenv("BACKLOG_REPORT_TIMEZONE", "Asia/Tokyo")
    assert _settings(tmp_path, ", timezone: UTC").timezone == "Asia/Tokyo"  # 環境変数が優先
    monkeypatch.setenv("BACKLOG_REPORT_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ValueError, match="タイムゾーン"):
        _settings(tmp_path)


def test_to_issue_dates_keep_calendar_date_and_convert_timestamps():
    raw = {"issueKey": "X-1", "dueDate": "2026-10-01T00:00:00Z", "updated": "2026-10-01T15:30:00Z", "created": "2026-09-30T14:59:00Z"}
    i = to_issue(raw, tz="Asia/Tokyo")
    assert i.due == date(2026, 10, 1)  # 日付の項目はそのまま
    assert (i.updated, i.created) == (date(2026, 10, 2), date(2026, 9, 30))
    assert to_issue(raw, tz="UTC").updated == date(2026, 10, 1)


def test_cli_today_uses_timezone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    st_path = tmp_path / "config.yaml"
    st_path.write_text("backlog: {project_key: X, mock_dir: m}\nreport: {output_dir: out, snapshot_file: data/s.csv}\n", encoding="utf-8")
    monkeypatch.setattr("backlog_report.config._utcnow", lambda: UTC_2350)
    monkeypatch.setattr("backlog_report.entrypoints.cli.make_port", lambda st, base: OneIssue())
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(main, ["--env", "/nonexistent/.env", "-c", str(st_path), "batch", "--no-charts"])
    assert rc == 0, err.getvalue()
    assert (tmp_path / "out/report_2026-10-02.md").is_file()
    assert "期限超過 1" in err.getvalue()


# --- 中: 記録の CSV をその場で上書きしない -------------------------------------------------------


def test_snapshot_record_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    p = tmp_path / "s.csv"
    snapshots.record(p, date(2026, 10, 1), [("M1", 5, 20.0)])
    before = p.read_bytes()

    def boom(self, rows):
        raise OSError("disk full")

    monkeypatch.setattr(csv.DictWriter, "writerows", boom)
    with pytest.raises(OSError):
        snapshots.record(p, date(2026, 10, 2), [("M1", 4, 16.0)])
    assert p.read_bytes() == before  # 途中で落ちても記録は残る
    assert [f.name for f in tmp_path.iterdir()] == ["s.csv"]  # 一時ファイルも残さない


# --- 低: ボットの例外 --------------------------------------------------------------------------


def _bot_env(tmp_path: Path):
    spec_dir = tmp_path / "samples"
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_samples", ROOT / "samples/make_samples.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.generate(spec_dir / "backlog")
    shutil.copy(ROOT / "samples/config.yaml", spec_dir / "config.yaml")
    return load_settings(spec_dir / "config.yaml"), MockBacklog(spec_dir / "backlog")


def test_slack_bot_unclosed_quote_returns_message(tmp_path: Path):
    st, port = _bot_env(tmp_path)
    res = slack_bot.handle('"フェーズ2', port, st)
    assert res["response_type"] == "ephemeral" and "引用符" in res["text"]


def test_slack_bot_backlog_error_returns_message(tmp_path: Path):
    st, _ = _bot_env(tmp_path)

    class Down:
        def issues(self):
            raise HttpError(503, "Service Unavailable")

        def versions(self):
            return []

    res = slack_bot.handle("", Down(), st)  # type: ignore[arg-type]
    assert res["response_type"] == "ephemeral" and "Backlog" in res["text"] and "503" in res["text"]


# --- 中・低: 定期実行の例 ----------------------------------------------------------------------


def _example() -> tuple[dict, str]:
    text_ = (ROOT / "docs/examples/scheduled-report.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text_), text_


def test_example_saves_snapshots_independently_of_notifications():
    wf, _ = _example()
    jobs = wf["jobs"]
    # 通知（Slack・Wiki）は記録を作るジョブとは別のジョブで行い、記録の保存はその成否に左右されない
    record_job = jobs["report"]
    assert "--slack" not in str(record_job) and "--wiki" not in str(record_job)
    notify = [n for n, j in jobs.items() if "--slack" in str(j)]
    assert notify and all(n != "save-snapshots" for n in notify)
    needs = jobs["save-snapshots"]["needs"]
    assert "report" in (needs if isinstance(needs, list) else [needs])
    if "notify" in str(needs):  # 送った記録を待つときも、通知の成否に関わらず保存する
        assert "always()" in jobs["save-snapshots"]["if"] and "needs.report.result == 'success'" in jobs["save-snapshots"]["if"]
    assert all("report" in str(jobs[n].get("needs")) for n in notify)


def test_example_pins_actions_and_constraints():
    wf, text_ = _example()
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for job in wf["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                ref = step["uses"].split("@", 1)[1]
                assert len(ref) == 40, step["uses"]  # コミット SHA で固定
                if step["uses"].split("@")[0] in ("actions/checkout", "actions/setup-python", "actions/upload-artifact"):
                    assert step["uses"] in ci  # 本体の CI と同じ SHA
    assert "-c constraints.txt" in text_
    assert "TZ=Asia/Tokyo" in text_ and "BACKLOG_REPORT_TIMEZONE" in text_


# --- 第 2 回: 記録の消失・mrkdwn のエスケープ・ガントの並び・未着手の状態名・二重投稿 ---------------------------------


def test_example_distinguishes_missing_branch_from_fetch_failure():
    """report-data の取得の失敗を「初回」とみなして記録を上書きで消さない（ブランチ無しの 2 とそれ以外を区別して止める）."""
    wf, text_ = _example()
    restore = next(st["run"] for st in wf["jobs"]["report"]["steps"] if "report-data" in str(st.get("run", "")))
    save = next(st["run"] for st in wf["jobs"]["save-snapshots"]["steps"] if "report-data" in str(st.get("run", "")))
    for script in (restore, save):
        assert "ls-remote --exit-code" in script
        assert '"$rc" = 2' in script and "exit 1" in script
        assert "if git fetch" not in script  # fetch の失敗で初回扱いにしない
    assert "switch --orphan" in save.split('"$rc" = 2', 1)[1].split("else", 1)[0]


def _ls_remote_case(tmp_path: Path, rc: int) -> tuple[int, str]:
    """例の restore の分岐を、git の代わりに決まった終了コードを返す偽物で走らせる."""
    import os
    import subprocess

    if shutil.which("bash") is None or os.name == "nt":
        pytest.skip("bash が必要")
    wf, _ = _example()
    script = next(st["run"] for st in wf["jobs"]["report"]["steps"] if "report-data" in str(st.get("run", "")))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "git"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'case "$*" in *ls-remote*) exit {rc};; *"show FETCH_HEAD:snapshots.csv"*) echo "date,milestone,remaining_count,remaining_hours";; esac\n'
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "TOKEN": "dummy"}
    res = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    return res.returncode, res.stdout + res.stderr


def test_example_restore_starts_empty_only_when_branch_is_missing(tmp_path: Path):
    rc, out = _ls_remote_case(tmp_path, 2)
    assert rc == 0 and "初回" in out
    assert (tmp_path / "data/posted.csv").read_text(encoding="utf-8").startswith("date,channel,project,item")


def test_example_restore_stops_on_network_failure(tmp_path: Path):
    rc, out = _ls_remote_case(tmp_path, 128)
    assert rc != 0 and "止めます" in out
    assert not (tmp_path / "data/snapshots.csv").exists()


def test_slack_blocks_escape_mrkdwn():
    from backlog_report.domain.model import Issue
    from backlog_report.domain.progress import Summary
    from backlog_report.rendering import text

    bad = Issue(
        "X-1",
        "a < b && c > d <!channel>",
        "未対応",
        "<@U0>",
        "タスク",
        (),
        None,
        date(2026, 10, 1),
        None,
        None,
        date(2026, 9, 1),
        date(2026, 9, 1),
        "",
    )
    s = Summary(date(2026, 10, 2), 1, 0, delayed=[bad])
    msg, blocks = text.slack_blocks(s, "P&Q", "https://example.backlog.com")
    body = blocks[-1]["text"]["text"]
    assert "a &lt; b &amp;&amp; c &gt; d &lt;!channel&gt;" in body
    assert "&lt;@U0&gt;" in body and "<!channel>" not in body
    assert "<https://example.backlog.com/view/X-1|X-1>" in body  # リンク自体は壊さない
    assert "P&amp;Q" in msg


def test_gantt_sorts_before_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """先頭の 40 件で切ってから並べていた: 並びの先頭（開始の早い課題）が抜けないこと."""
    from backlog_report.domain.model import Issue
    from backlog_report.rendering import charts

    def mk(n: int, start: date) -> Issue:
        return Issue(
            f"X-{n}", f"課題{n}", "処理中", "A", "タスク", (), start, date(2026, 12, 1), None, None, date(2026, 9, 1), date(2026, 9, 1), ""
        )

    late = [mk(n, date(2026, 11, 1)) for n in range(1, 41)]
    early = mk(99, date(2026, 9, 1))  # API の返す順では 41 件目
    drawn: list[str] = []
    real = charts.plt.subplots

    def spy(*a, **k):
        fig, ax = real(*a, **k)
        orig = ax.set_yticks

        def set_yticks(ticks, labels=None, **kw):
            drawn.extend(labels or [])
            return orig(ticks, labels, **kw)

        ax.set_yticks = set_yticks
        return fig, ax

    monkeypatch.setattr(charts.plt, "subplots", spy)
    assert charts.gantt(late + [early], date(2026, 10, 2), ["完了"], tmp_path / "g.png", "g", limit=40)
    assert len(drawn) == 40 and drawn[0].startswith("X-99 ")


def test_first_status_is_configurable(tmp_path: Path):
    st = _settings(tmp_path)
    assert st.first_status == "未対応"  # 既定は今と同じ
    p = tmp_path / "config.yaml"
    p.write_text(
        f"backlog: {{project_key: X, mock_dir: m}}\nreport: {{snapshot_file: {tmp_path.as_posix()}/s.csv, first_status: 新規}}\n",
        encoding="utf-8",
    )
    st = load_settings(p)
    assert st.first_status == "新規"

    class Port(OneIssue):
        def issues(self):
            rec = super().issues()[0]
            return [{**rec, "status": {"name": "新規"}, "startDate": "2026-09-01T00:00:00Z", "dueDate": "2026-10-31T00:00:00Z"}]

    r = report.build(Port(), st, today=date(2026, 10, 2))
    assert [i.key for i in r.summary.not_started] == ["X-1"]
    st.first_status = "未対応"
    assert report.build(Port(), st, today=date(2026, 10, 2)).summary.not_started == []


class _Http:
    def __init__(self, fail: str = ""):
        self.urls: list[str] = []
        self.fail = fail

    def __call__(self, req):
        self.urls.append(req.full_url)
        if self.fail and req.full_url.endswith(self.fail):
            return {"ok": False, "error": "fatal_error"}
        if req.full_url.endswith("files.getUploadURLExternal"):
            return {"ok": True, "upload_url": "https://files.example/upload", "file_id": "F1"}
        return {"ok": True}


def test_post_slack_does_not_post_twice(tmp_path: Path):
    from backlog_report.infrastructure.slack import SlackClient, SlackError

    st = _settings(tmp_path)
    st.slack_channel = "C1"
    st.post_log = str(tmp_path / "data/posted.csv")
    r = report.build(OneIssue(), st, today=date(2026, 10, 2))
    png = tmp_path / "gantt.png"
    png.write_bytes(b"png")
    r.files = {"gantt": png}
    # 1 回目: 本文は送れたが、画像のアップロードで失敗
    http = _Http(fail="completeUploadExternal")
    with pytest.raises(SlackError):
        report.post_slack(r, st, SlackClient(bot_token="xoxb-test", http=http))
    assert sum(u.endswith("chat.postMessage") for u in http.urls) == 1
    # 再実行: 本文は送り直さず、画像だけ送る
    http = _Http()
    assert report.post_slack(r, st, SlackClient(bot_token="xoxb-test", http=http)) == ["message"]
    assert not any(u.endswith("chat.postMessage") for u in http.urls)
    assert sum(u.endswith("completeUploadExternal") for u in http.urls) == 1
    # もう一度: 何も送らない
    http = _Http()
    assert report.post_slack(r, st, SlackClient(bot_token="xoxb-test", http=http)) == ["message", "file:gantt.png"]
    assert http.urls == []
    # 別の日・--force-post なら送る
    http = _Http()
    report.post_slack(r, st, SlackClient(bot_token="xoxb-test", http=http), force=True)
    assert sum(u.endswith("chat.postMessage") for u in http.urls) == 1


def test_cli_batch_slack_twice_posts_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import backlog_report.infrastructure.slack as slack_mod

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "backlog: {project_key: X, mock_dir: m}\nreport: {today: 2026-10-02}\nslack: {channel: C1}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("backlog_report.entrypoints.cli.make_port", lambda st, base: OneIssue())
    http = _Http()
    monkeypatch.setattr(slack_mod, "_default_http", http)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    def go(*extra: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = run_main(main, ["--env", "/nonexistent/.env", "-c", str(cfg), "batch", "--slack", "--no-charts", *extra])
        return rc, out.getvalue()

    assert go()[0] == 0
    rc, out = go()
    assert rc == 0 and "送りませんでした" in out
    assert sum(u.endswith("chat.postMessage") for u in http.urls) == 1
    assert (tmp_path / "data/posted.csv").read_text(encoding="utf-8").count("message") == 1
    assert go("--force-post")[0] == 0
    assert sum(u.endswith("chat.postMessage") for u in http.urls) == 2


def test_numeric_log_format_survives_redact_filter():
    """devtools-common の RedactFilter で %d のログが消えていた件（28bab4c で修正）の再現テスト."""
    import logging

    from devtools_common.log import RedactFilter

    buf, err = io.StringIO(), io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactFilter())
    logger = logging.getLogger("backlog_report.test")
    logger.addHandler(handler)
    try:
        with contextlib.redirect_stderr(err):
            logger.warning("記録 %d 件", 3)
    finally:
        logger.removeHandler(handler)
    assert "記録 3 件" in buf.getvalue() and "Logging error" not in err.getvalue()
