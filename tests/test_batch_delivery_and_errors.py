"""batch の届け方（Slack が失敗しても Wiki は更新する）と、データ側の誤りの終了コード（3）・/progress の利用者制限."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
from pathlib import Path

import pytest
from devtools_common.cli import run_main

from backlog_report.config import Settings, load_settings
from backlog_report.entrypoints import slack_bot
from backlog_report.entrypoints.cli import main
from backlog_report.infrastructure.backlog import MockBacklog

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("make_samples", ROOT / "samples/make_samples.py")
assert _spec and _spec.loader
make_samples = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_samples)
WIKI = "samples/backlog/wiki/進捗レポート__最新.md"


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


def _failing_slack(monkeypatch: pytest.MonkeyPatch) -> list:
    import backlog_report.infrastructure.slack as slack_mod

    calls: list = []

    def http(req):
        calls.append(req)
        return {"ok": False, "error": "channel_not_found"}

    monkeypatch.setattr(slack_mod, "_default_http", http)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    return calls


def test_wiki_is_updated_even_if_slack_fails(env: Path, monkeypatch: pytest.MonkeyPatch):
    calls = _failing_slack(monkeypatch)
    rc, out = run("-c", str(env / "samples/config.yaml"), "batch", "--slack", "--wiki", "--no-charts")
    assert calls  # Slack には送ろうとした
    assert rc == 3, out  # 終了コードは従来どおり Slack の失敗（実行時エラー）
    assert (env / WIKI).is_file()  # Wiki は更新した
    assert "Slack への投稿が失敗しました" in out
    assert "channel_not_found" in out


def test_slack_failure_without_wiki_is_unchanged(env: Path, monkeypatch: pytest.MonkeyPatch):
    _failing_slack(monkeypatch)
    rc, out = run("-c", str(env / "samples/config.yaml"), "batch", "--slack", "--no-charts")
    assert rc == 3, out
    assert "Wiki の更新は続けます" not in out
    assert not (env / WIKI).exists()


def test_wiki_failure_after_slack_failure_reports_both(env: Path, monkeypatch: pytest.MonkeyPatch):
    _failing_slack(monkeypatch)

    def broken(self, name, content):
        raise OSError("disk full")

    monkeypatch.setattr(MockBacklog, "save_wiki", broken)
    rc, out = run("-c", str(env / "samples/config.yaml"), "batch", "--slack", "--wiki", "--no-charts")
    assert rc == 3, out
    assert "Wiki の更新も失敗しました: OSError: disk full" in out
    assert "channel_not_found" in out  # 最後は Slack の失敗で終わる


def _break_first_issue(env: Path, field: str, value) -> None:
    path = env / "samples/backlog/issues.json"
    issues = json.loads(path.read_text(encoding="utf-8"))
    issues[0][field] = value
    path.write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("command", [["report"], ["batch", "--no-charts"]])
def test_unreadable_issue_date_is_runtime_error(env: Path, command: list[str]):
    """課題のデータの誤りは設定・引数の誤り（2）ではなく実行時エラー（3）."""
    _break_first_issue(env, "dueDate", "2026/13/45")
    rc, out = run("-c", str(env / "samples/config.yaml"), *command)
    assert rc == 3, out
    assert "課題のデータが読めません" in out
    assert "使い方" not in out


def test_missing_issue_key_is_runtime_error(env: Path):
    path = env / "samples/backlog/issues.json"
    issues = json.loads(path.read_text(encoding="utf-8"))
    del issues[0]["issueKey"]
    path.write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
    rc, out = run("-c", str(env / "samples/config.yaml"), "report")
    assert rc == 3, out
    assert "issueKey" in out


def test_broken_mock_json_is_runtime_error(env: Path):
    (env / "samples/backlog/issues.json").write_text("{not json", encoding="utf-8")
    rc, out = run("-c", str(env / "samples/config.yaml"), "report")
    assert rc == 3, out
    assert "JSON として読めません" in out


def test_unknown_milestone_is_still_usage_error(env: Path):
    rc, out = run("-c", str(env / "samples/config.yaml"), "report", "--milestone", "存在しない")
    assert rc == 2, out
    assert "マイルストーンがありません" in out


# --- /progress の利用者・チャンネルの制限 ---


class CountingPort(MockBacklog):
    def __init__(self, directory: Path):
        super().__init__(directory)
        self.reads = 0

    def issues(self):
        self.reads += 1
        return super().issues()


def _bot_settings(env: Path, extra: str = "") -> tuple[Settings, CountingPort]:
    cfg = env / "bot.yaml"
    cfg.write_text(
        "backlog: {project_key: SAMPLE, mock_dir: samples/backlog}\nreport: {today: 2026-10-02}\nslack:\n  channel: C1\n" + extra,
        encoding="utf-8",
    )
    return load_settings(cfg), CountingPort(env / "samples/backlog")


def test_bot_without_allowlist_is_unrestricted(env: Path):
    st, port = _bot_settings(env)
    assert st.bot_allowed_users == [] and st.bot_allowed_channels == []
    res = slack_bot.handle("", port, st, user_id="U9", channel_id="C9")
    assert "blocks" in res and port.reads == 1


def test_bot_denies_user_not_in_allowlist_without_reading(env: Path):
    st, port = _bot_settings(env, "  allowed_users: [U1, U2]\n")
    res = slack_bot.handle("--channel", port, st, user_id="U9", channel_id="C1")
    assert res["response_type"] == "ephemeral"  # --channel でも断りは本人だけに
    assert "権限がありません" in res["text"]
    assert port.reads == 0  # 課題を読まない
    ok = slack_bot.handle("", port, st, user_id="U2", channel_id="C1")
    assert "blocks" in ok


def test_bot_channel_allowlist(env: Path):
    st, port = _bot_settings(env, "  allowed_channels: C1\n")  # 1 つなら文字列でもよい
    assert st.bot_allowed_channels == ["C1"]
    assert "権限がありません" in slack_bot.handle("", port, st, user_id="U1", channel_id="C2")["text"]
    assert "blocks" in slack_bot.handle("", port, st, user_id="U1", channel_id="C1")


def test_bot_both_allowlists_must_match(env: Path):
    st, port = _bot_settings(env, "  allowed_users: [U1]\n  allowed_channels: [C1]\n")
    assert not slack_bot.allowed(st, "U1", "C2")
    assert not slack_bot.allowed(st, "U2", "C1")
    assert slack_bot.allowed(st, "U1", "C1")
    assert not slack_bot.allowed(st)  # ID が来ないときは断る


def test_bot_allowlist_rejects_mapping(env: Path):
    with pytest.raises(ValueError, match="slack.allowed_users"):
        _bot_settings(env, "  allowed_users: {a: 1}\n")
