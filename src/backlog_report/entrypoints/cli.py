"""backlog-report — Backlog の課題から進捗レポートを作る. 層 1: 入口.

  backlog-report report                       # その場で確認（online）: Markdown を標準出力へ
  backlog-report report --milestone "v1.0" --assignee "担当A"
  backlog-report batch --slack --wiki         # 定期実行（batch）: ファイル出力・残量の記録・Slack 投稿・Wiki 更新
  backlog-report bot                          # Slack ボット（Socket Mode）: /progress で応答

設定は config.yaml（-c で変更）、秘密情報は .env（BACKLOG_API_KEY・SLACK_BOT_TOKEN など）。
backlog.mock_dir を設定すると API の代わりに JSON を読む（試行用。samples/backlog）。
--today に今日以外の日付（今日より前・今日より後）を指定した batch は、レポートは作るが残量の記録（snapshots.csv）と
Slack に送った記録（posted.csv）を付けない（その日の記録を今の課題の状態で上書きしない・まだ来ていない日の記録を作らないため）。
batch --slack --wiki で Slack への投稿が失敗しても、Wiki の更新は行う（そのうえで Slack の失敗の終了コードで終わる）。
終了コード: 0 正常 / 1 期限超過あり（--fail-on-delay）/ 2 設定・引数の誤り（API キー・プロジェクトキーの誤り＝ Backlog の 401/403/404 を含む）
/ 3 実行時エラー（API・Slack の失敗など）
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from devtools_common import cli as common_cli
from devtools_common.config import load_dotenv
from devtools_common.httpclient import HttpError

from .. import __version__
from ..application import report
from ..config import Settings, load_settings, local_today, secret
from ..infrastructure.backlog import BacklogApi, BacklogPort, BacklogResponseError, MockBacklog
from ..infrastructure.slack import SlackClient

# Backlog が設定の誤りで返す状態コード: 401 API キーが無効 / 403 そのプロジェクトを見る権限が無い / 404 プロジェクトキー・space_url の誤り
CONFIG_ERROR_STATUSES = {401: "API キー（BACKLOG_API_KEY）", 403: "API キーの利用者の権限", 404: "backlog.project_key・backlog.space_url"}


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
    b.add_argument("--force-post", action="store_true", help="今日すでに送ったレポートでも Slack に送り直す（既定は送り済みなら飛ばす）")
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
    for attr in ("output_dir", "snapshot_file", "post_log"):
        if getattr(st, attr) and not Path(getattr(st, attr)).is_absolute():
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
            not_today = _not_today_warning(args.today, local_today(st.timezone), Path(st.snapshot_file).name)
            if not_today:
                r.warnings.append(not_today)
            elif not args.no_snapshot:
                report.record_snapshot(r, st)
            print(f"レポート: {r.files['markdown']}（グラフ {len(r.files) - 1} 枚）")
            for w in r.warnings:
                print(f"警告: {w}", file=sys.stderr)
            slack_error: Exception | None = None
            if args.slack:
                try:
                    _post_slack(r, st, force=args.force_post, record=not not_today)
                except Exception as e:  # noqa: BLE001 — Wiki を更新してから同じ例外で終わる（終了コードは従来どおり）
                    if not args.wiki:
                        raise
                    slack_error = e
                    print("警告: Slack への投稿が失敗しました。Wiki の更新は続けます", file=sys.stderr)
            if args.wiki:
                try:
                    print(f"Wiki を更新しました: {report.update_wiki(r, st, port)}")
                except Exception as e:
                    if slack_error is None:
                        raise
                    print(f"警告: Wiki の更新も失敗しました: {type(e).__name__}: {e}", file=sys.stderr)
            if slack_error is not None:
                raise slack_error
    except HttpError as e:
        if e.status in CONFIG_ERROR_STATUSES:  # 認証・プロジェクトキーの誤り: 再実行では直らないので設定の誤り（2）
            raise common_cli.UsageError(
                f"{e}（Backlog が {e.status} を返しました。{CONFIG_ERROR_STATUSES[e.status]} を確かめてください）"
            ) from None
        raise
    except BacklogResponseError as e:  # 200 でも JSON でない応答など: 設定の誤り（2）ではなく実行時エラー（3）
        print(f"error: {e}", file=sys.stderr)
        return common_cli.ExitCode.RUNTIME
    except ValueError as e:  # 引数・設定の誤り（課題のデータが読めないときは BacklogResponseError＝ 3）
        raise common_cli.UsageError(str(e)) from None
    s = r.summary
    print(f"完了 {s.done}/{s.total} ／ 期限超過 {len(s.delayed)} ／ 期限間近 {len(s.due_soon)}", file=sys.stderr)
    return common_cli.ExitCode.FINDINGS if args.fail_on_delay and s.delayed else common_cli.ExitCode.OK


def _not_today_warning(given: date | None, today: date, snapshot_name: str) -> str:
    """--today が今日（設定のタイムゾーン）以外なら、記録を付けない理由の警告. 今日か未指定なら空文字."""
    if not given or given == today:
        return ""
    if given < today:
        return (
            f"--today {given} は今日より前の日付なので、残量の記録（{snapshot_name}）と"
            "Slack に送った記録を付けません（その日の記録を今の課題の状態で上書きしないため）"
        )
    return (
        f"--today {given} は今日（{today}）より後の日付なので、残量の記録（{snapshot_name}）と"
        "Slack に送った記録を付けません（まだ来ていない日の記録を作ると、その日の実際の記録が付かなくなるため）"
    )


def _post_slack(r: report.Report, st: Settings, *, force: bool, record: bool) -> None:
    client = SlackClient(bot_token=_opt("SLACK_BOT_TOKEN"), webhook_url=_opt("SLACK_WEBHOOK_URL"))
    skipped = report.post_slack(r, st, client, force=force, record=record)
    if skipped:
        print(
            f"Slack: 今日すでに送ったものは送りませんでした（{', '.join(skipped)}。送り直すときは --force-post）",
            file=sys.stderr,
        )
    print(f"Slack に投稿しました: {st.slack_channel or 'Webhook'}" + ("（送り済みのものを除く）" if skipped else ""))


def _opt(name: str) -> str:
    try:
        return secret(name)
    except ValueError:
        return ""


def entrypoint() -> None:
    sys.exit(common_cli.run_main(main))
