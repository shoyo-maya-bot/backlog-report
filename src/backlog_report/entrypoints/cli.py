"""backlog-report — Backlog の課題から進捗レポートを作る. 層 1: 入口.

  backlog-report report                       # その場で確認（online）: Markdown を標準出力へ
  backlog-report report --milestone "v1.0" --assignee "担当A"
  backlog-report batch --slack --wiki         # 定期実行（batch）: ファイル出力・残量の記録・Slack 投稿・Wiki 更新
  backlog-report bot                          # Slack ボット（Socket Mode）: /progress で応答

設定は config.yaml（-c で変更）、秘密情報は .env（BACKLOG_API_KEY・SLACK_BOT_TOKEN など）。
backlog.mock_dir を設定すると API の代わりに JSON を読む（試行用。samples/backlog）。
終了コード: 0 正常 / 1 期限超過あり（--fail-on-delay）/ 2 設定・引数の誤り / 3 実行時エラー（API・Slack の失敗など）
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from devtools_common import cli as common_cli
from devtools_common.config import load_dotenv

from .. import __version__
from ..application import report
from ..config import Settings, load_settings, secret
from ..infrastructure.backlog import BacklogApi, BacklogPort, MockBacklog
from ..infrastructure.slack import SlackClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="backlog-report", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-c", "--config", type=Path, default=Path("config.yaml"))
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--today", type=date.fromisoformat, help="基準日（YYYY-MM-DD。既定は今日）")
    p.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("report", help="その場でレポートを表示する")
    r.add_argument("--milestone", default="")
    r.add_argument("--assignee", default="")
    r.add_argument("-o", "--output", default="-", help="出力先（既定: 標準出力）")
    r.add_argument("--fail-on-delay", action="store_true")
    b = sub.add_parser("batch", help="定期実行: ファイル出力・残量の記録・通知")
    b.add_argument("--slack", action="store_true", help="Slack に投稿する")
    b.add_argument("--wiki", action="store_true", help="Backlog Wiki を更新する")
    b.add_argument("--no-charts", action="store_true")
    b.add_argument("--no-snapshot", action="store_true", help="残量を記録しない")
    b.add_argument("--fail-on-delay", action="store_true")
    sub.add_parser("bot", help="Slack ボットを起動する（Socket Mode）")
    return p


def make_port(st: Settings, base: Path) -> BacklogPort:
    if st.mock_dir:
        p = Path(st.mock_dir)
        return MockBacklog(p if p.is_absolute() else base / p)
    return BacklogApi(st.space_url, st.project_key, secret("BACKLOG_API_KEY"))


def _settings(args) -> Settings:
    load_dotenv(args.env)
    try:
        st = load_settings(args.config)
    except (FileNotFoundError, ValueError) as e:
        raise common_cli.UsageError(str(e)) from None
    if args.today:
        st.today = args.today
    return st


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    common_cli.configure_from_args(args)
    st = _settings(args)
    base = args.config.resolve().parent
    # 相対パスは設定ファイルの場所から
    for attr in ("output_dir", "snapshot_file"):
        if not Path(getattr(st, attr)).is_absolute():
            setattr(st, attr, str(base / getattr(st, attr)))
    try:
        port = make_port(st, base)
    except (FileNotFoundError, ValueError) as e:
        raise common_cli.UsageError(str(e)) from None

    if args.command == "bot":
        from . import slack_bot

        slack_bot.run(lambda: make_port(st, base), st)
        return common_cli.ExitCode.OK

    try:
        if args.command == "report":
            r = report.build(port, st, milestone=args.milestone, assignee=args.assignee)
            from ..rendering import text

            common_cli.write_output(text.markdown(r.summary, r.project, space_url=st.space_url), args.output)
        else:
            r = report.build(port, st)
            report.write_files(r, st, Path(st.output_dir), with_charts=not args.no_charts)
            if not args.no_snapshot:
                report.record_snapshot(r, st)
            print(f"レポート: {r.files['markdown']}（グラフ {len(r.files) - 1} 枚）")
            if args.slack:
                client = SlackClient(bot_token=_opt("SLACK_BOT_TOKEN"), webhook_url=_opt("SLACK_WEBHOOK_URL"))
                report.post_slack(r, st, client)
                print(f"Slack に投稿しました: {st.slack_channel or 'Webhook'}")
            if args.wiki:
                print(f"Wiki を更新しました: {report.update_wiki(r, st, port)}")
    except ValueError as e:
        raise common_cli.UsageError(str(e)) from None
    s = r.summary
    print(f"完了 {s.done}/{s.total} ／ 期限超過 {len(s.delayed)} ／ 期限間近 {len(s.due_soon)}", file=sys.stderr)
    return common_cli.ExitCode.FINDINGS if args.fail_on_delay and s.delayed else common_cli.ExitCode.OK


def _opt(name: str) -> str:
    try:
        return secret(name)
    except ValueError:
        return ""


def entrypoint() -> None:
    sys.exit(common_cli.run_main(main))
