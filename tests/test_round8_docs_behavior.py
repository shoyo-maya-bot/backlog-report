"""第 8 回: README・CHANGELOG・ヘルプに書いた使い方と実際の動きのずれ."""

from __future__ import annotations

import contextlib
import io
import re
import shlex
import shutil
from datetime import date
from pathlib import Path

import pytest
from devtools_common.cli import ExitCode, run_main

from backlog_report.entrypoints import cli, slack_bot
from backlog_report.infrastructure import posts

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def run(*args: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(cli.main, ["--env", "/nonexistent/.env", *args])
    return rc, out.getvalue() + err.getvalue()


def documented_commands() -> list[str]:
    """README のコードブロック・インラインコードと cli の docstring にある `backlog-report ...` の例."""
    texts = [README, cli.__doc__ or ""]
    cmds: list[str] = []
    for t in texts:
        for line in t.splitlines():
            for m in re.finditer(r"(?:^|`)\s*(backlog-report [a-z-][^`#]*)", line):
                cmds.append(m.group(1).strip())
    return cmds


def test_documented_commands_parse():
    """書いてある使い方が argparse で通る（以前は README の `batch --today` が unrecognized arguments で終了コード 2）."""
    cmds = documented_commands()
    assert any("--today" in c for c in cmds)
    parser = cli.build_parser()
    for cmd in cmds:
        argv = shlex.split(cmd)[1:]
        try:
            parser.parse_args(argv)
        except SystemExit as e:  # pragma: no cover — 失敗時の表示
            pytest.fail(f"README・ヘルプの例が通りません: {cmd}（{e}）")


@pytest.mark.parametrize(
    "argv",
    [
        ["batch", "--today", "2026-10-01"],
        ["--today", "2026-10-01", "batch"],
        ["report", "--today", "2026-10-01"],
        ["--today", "2026-10-01", "report"],
    ],
)
def test_today_before_or_after_subcommand(argv):
    assert cli.build_parser().parse_args(argv).today == date(2026, 10, 1)


def test_today_after_subcommand_wins_and_absent_keeps_global():
    p = cli.build_parser()
    assert p.parse_args(["--today", "2026-10-01", "batch", "--today", "2026-10-02"]).today == date(2026, 10, 2)
    assert p.parse_args(["batch"]).today is None


def test_report_with_today_after_subcommand_runs(tmp_path: Path):
    shutil.copytree(ROOT / "samples", tmp_path / "samples")
    rc, out = run("-c", str(tmp_path / "samples/config.yaml"), "report", "--today", "2026-10-01")
    assert rc == ExitCode.OK, out
    assert "2026-10-01" in out


def test_bot_without_tokens_is_config_error(monkeypatch: pytest.MonkeyPatch):
    """README の終了コード表どおり、SLACK_BOT_TOKEN が無い bot は 2（以前は 3）."""
    pytest.importorskip("slack_bolt")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    rc, out = run("-c", str(ROOT / "samples/config.yaml"), "bot")
    assert rc == ExitCode.USAGE, out
    assert "SLACK_BOT_TOKEN" in out


def test_bot_without_app_token_fails_before_connecting(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("slack_bolt")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    rc, out = run("-c", str(ROOT / "samples/config.yaml"), "bot")
    assert rc == ExitCode.USAGE, out
    assert "SLACK_APP_TOKEN" in out


def test_posted_csv_with_bom_is_read(tmp_path: Path):
    p = tmp_path / "posted.csv"
    p.write_text("﻿date,channel,project,item\n2026-10-01,C1,SAMPLE,message\n", encoding="utf-8")
    assert posts.load(p) == {("2026-10-01", "C1", "SAMPLE", "message")}
    posts.add(p, ("2026-10-02", "C1", "SAMPLE", "message"))
    assert len(posts.load(p)) == 2


def test_names_with_spaces_need_quotes_documented():
    assert "引用符" in slack_bot.HELP
    assert '/progress "フェーズ2 製造"' in README
    assert "空白を含むマイルストーン名・担当者名は引用符で囲みます" in README


def test_quoted_names_are_parsed_as_one():
    class Port:  # report.build に渡る前の引数の分け方だけを見る
        pass

    seen = {}

    def fake_build(port, st, *, milestone="", assignee=""):
        seen.update(milestone=milestone, assignee=assignee)
        raise ValueError("stop")

    from backlog_report.application import report
    from backlog_report.config import Settings

    orig = report.build
    report.build = fake_build
    try:
        slack_bot.handle('"フェーズ2 製造" "@担当 A"', Port(), Settings(space_url="", project_key=""))
    finally:
        report.build = orig
    assert seen == {"milestone": "フェーズ2 製造", "assignee": "担当 A"}


def test_setup_scripts_use_constraints():
    assert "-c constraints.txt" in (ROOT / "setup.sh").read_text(encoding="utf-8")
    cmd = (ROOT / "setup.cmd").read_bytes().decode("cp932")
    assert "-c constraints.txt" in cmd and "%C%" in cmd
    assert "sys.version_info < (3, 12)" in cmd
