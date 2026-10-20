"""batch に過去の日付（--today）を指定したとき、その日の記録（残量・Slack に送った記録）を今の状態で上書きしない."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from devtools_common.cli import run_main

from backlog_report.entrypoints.cli import main

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # 日本時間 2026-10-05
_spec = importlib.util.spec_from_file_location("make_samples", ROOT / "samples/make_samples.py")
assert _spec and _spec.loader
make_samples = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_samples)

HEADER = "date,milestone,remaining_count,remaining_hours,unit\n"
RECORDED = "2026-10-01,フェーズ2 製造,99,99.0,count\n"


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    make_samples.generate(tmp_path / "samples/backlog")
    shutil.copy(ROOT / "samples/config.yaml", tmp_path / "samples/config.yaml")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/snapshots.csv").write_text(HEADER + RECORDED, encoding="utf-8")
    monkeypatch.setattr("backlog_report.config._utcnow", lambda: NOW)
    return tmp_path


def run(*args: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(main, ["--env", "/nonexistent/.env", *args])
    return rc, out.getvalue() + err.getvalue()


class FakeSlack:
    def __init__(self):
        self.requests: list = []

    def __call__(self, req):
        self.requests.append(req)
        if req.full_url.endswith("files.getUploadURLExternal"):
            return {"ok": True, "upload_url": "https://files.example/upload", "file_id": "F1"}
        return {"ok": True}


def test_past_today_keeps_that_days_snapshot(env: Path):
    rc, out = run("-c", str(env / "samples/config.yaml"), "--today", "2026-10-01", "batch", "--no-charts")
    assert rc == 0, out
    assert (env / "output/report_2026-10-01.md").is_file()  # レポートは作る
    assert (env / "data/snapshots.csv").read_text(encoding="utf-8") == HEADER + RECORDED  # 記録はそのまま
    assert "今日より前の日付" in out


def test_past_today_does_not_mark_slack_as_sent(env: Path, monkeypatch: pytest.MonkeyPatch):
    import backlog_report.infrastructure.slack as slack_mod

    http = FakeSlack()
    monkeypatch.setattr(slack_mod, "_default_http", http)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    rc, out = run("-c", str(env / "samples/config.yaml"), "--today", "2026-10-01", "batch", "--slack", "--no-charts")
    assert rc == 0, out
    assert http.requests  # 送ることはできる
    assert not (env / "samples/data/posted.csv").exists()  # その日に送った記録は付けない（post_log の既定は設定ファイルからの相対）


def test_today_or_later_still_records(env: Path):
    rc, out = run("-c", str(env / "samples/config.yaml"), "--today", "2026-10-05", "batch", "--no-charts")
    assert rc == 0, out
    assert "2026-10-05,フェーズ2 製造," in (env / "data/snapshots.csv").read_text(encoding="utf-8")
    assert "今日より前の日付" not in out


def test_future_today_does_not_record(env: Path, monkeypatch: pytest.MonkeyPatch):
    """未来の日付の記録を作ると、その日になって実際に実行しても送り済み扱いになるため、記録しない."""
    import backlog_report.infrastructure.slack as slack_mod

    http = FakeSlack()
    monkeypatch.setattr(slack_mod, "_default_http", http)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    rc, out = run("-c", str(env / "samples/config.yaml"), "--today", "2026-10-09", "batch", "--slack", "--no-charts")
    assert rc == 0, out
    assert (env / "output/report_2026-10-09.md").is_file()  # レポートは作る
    assert (env / "data/snapshots.csv").read_text(encoding="utf-8") == HEADER + RECORDED  # 未来の日の記録は付けない
    assert not (env / "samples/data/posted.csv").exists()
    assert "今日（2026-10-05）より後の日付" in out
