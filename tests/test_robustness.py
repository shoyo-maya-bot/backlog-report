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
    assert jobs["save-snapshots"]["needs"] == "report" or jobs["save-snapshots"]["needs"] == ["report"]
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
