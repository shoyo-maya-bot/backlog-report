"""レポートの文章（Markdown・Backlog Wiki・Slack のブロック）. 層 5: 表示."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..domain.model import Issue
from ..domain.progress import Summary

MAX_ROWS = 15


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _state(ratio: float, expected: float | None) -> str:
    if expected is None:
        return ""
    gap = ratio - expected
    return "🟢 順調" if gap >= -0.05 else "🟡 やや遅れ" if gap >= -0.2 else "🔴 遅れ"


def _issue_rows(issues: list[Issue], today: date, link) -> list[str]:
    rows = ["| 課題 | 件名 | 担当 | 状態 | 期限 | 超過 |", "|---|---|---|---|---|---|"]
    for i in issues[:MAX_ROWS]:
        over = f"{(today - i.due).days} 日" if i.due and i.due < today else ""
        rows.append(
            f"| {link(i.key)} | {i.summary.replace('|', '／')} | {i.assignee or '（未割当）'} | {i.status} | {i.due or ''} | {over} |"
        )
    if len(issues) > MAX_ROWS:
        rows.append(f"| … | ほか {len(issues) - MAX_ROWS} 件 | | | | |")
    return rows


def markdown(s: Summary, project: str, *, space_url: str = "", charts: dict[str, str] | None = None) -> str:
    def link(key: str) -> str:
        return f"[{key}]({space_url}/view/{key})" if space_url else key

    lines = [
        f"# 進捗レポート {project}（{s.today}）",
        "",
        f"- 課題: {s.total} 件（完了 {s.done} 件・{_pct(s.done / s.total if s.total else 0)}）",
        f"- 期限超過: **{len(s.delayed)} 件** ／ 期限間近: {len(s.due_soon)} 件 ／ 開始日を過ぎて未着手: {len(s.not_started)} 件 ／ 期限なし: {len(s.no_due)} 件",
        "",
        "## マイルストーン",
        "",
        "| マイルストーン | 期間 | 完了（件数） | 完了（見積時間） | 経過 | 状況 | 期限超過 |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in s.milestones:
        exp = m.expected_ratio(s.today)
        period = f"{m.start or '?'} 〜 {m.due or '?'}"
        lines.append(
            f"| {m.name} | {period} | {m.done}/{m.total}（{_pct(m.ratio)}） | {_pct(m.hours_ratio)} | {_pct(exp)} | {_state(m.ratio, exp)} | {m.delayed} |"
        )
    lines += ["", "## 期限超過", ""] + (_issue_rows(s.delayed, s.today, link) if s.delayed else ["なし"])
    lines += ["", "## 期限間近", ""] + (_issue_rows(s.due_soon, s.today, link) if s.due_soon else ["なし"])
    if s.not_started:
        lines += ["", "## 開始日を過ぎて未着手", ""] + _issue_rows(s.not_started, s.today, link)
    lines += ["", "## 担当者別", "", "| 担当 | 未完了 | 期限超過 | 期限間近 | 残り見積時間 |", "|---|---|---|---|---|"]
    lines += [f"| {a.name} | {a.open} | {a.delayed} | {a.due_soon} | {a.remaining_hours:.1f} h |" for a in s.assignees]
    for title, path in (charts or {}).items():
        lines += ["", f"## {title}", "", f"![{title}]({path})"]
    lines += [
        "",
        "> 「経過」は期間に対する日数の割合、「状況」は完了率と経過の差で判定しています（±5% 以内で順調、20% 以上の遅れで遅れ）。",
        "",
    ]
    return "\n".join(lines)


def wiki(s: Summary, project: str, space_url: str = "") -> str:
    """Backlog Wiki（Markdown 記法のプロジェクト向け）. 画像は載せない."""
    body = markdown(s, project, space_url=space_url)
    return (
        body
        + f"\n---\nこのページは backlog-report が自動で更新しています（最終更新 {s.today}）。手で編集しても次回の実行で上書きされます。\n"
    )


def mrkdwn_escape(value: str) -> str:
    """Slack の mrkdwn で特別な意味を持つ & < > を実体参照にする（件名の「<」で書式が崩れたり、<!channel> で通知が飛んだりしないように）.

    Slack の決まりどおり、この 3 文字だけを置き換える（* や _ などはそのまま）。
    """
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Slack の上限（https://api.slack.com/reference/block-kit/blocks）. 超えると invalid_blocks で投稿そのものが失敗する
SLACK_MAX_BLOCKS = 50  # 1 メッセージのブロック数
SLACK_SECTION_TEXT = 3000  # section の text の文字数
SLACK_HEADER_TEXT = 150  # header の plain_text の文字数
SLACK_DELAYED_ROWS = 10  # 期限超過の一覧に載せる件数


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def fit_lines(title: str, lines: list[str], *, hidden: int = 0, limit: int = SLACK_SECTION_TEXT) -> str:
    """見出しと行を、上限の文字数（limit）に収まるだけ載せる. 載らなかった行と hidden 件は「…ほか N 件」にまとめる.

    1 行目から入らないほど長い行は、切り詰めて載せる。行が無ければ「なし」。
    """
    if not lines and not hidden:
        return f"{title}\nなし"

    def build(kept: list[str]) -> str:
        rest = len(lines) - len(kept) + hidden
        return "\n".join([title, *kept] + ([f"…ほか {rest} 件"] if rest else []))

    kept: list[str] = []
    for line in lines:
        if len(build([*kept, line])) > limit:
            break
        kept.append(line)
    if not kept and lines:  # 1 行目から入らない: 切り詰めて 1 行だけ載せる
        over = len(build([lines[0]])) - limit
        kept = [_clip(lines[0], max(1, len(lines[0]) - over))]
    return build(kept)


def limit_blocks(blocks: list[dict[str, Any]], limit: int = SLACK_MAX_BLOCKS) -> list[dict[str, Any]]:
    """ブロック数を上限に収める. 超えた分は省き、最後に「ほか N ブロックを省略」を載せる."""
    if len(blocks) <= limit:
        return blocks
    dropped = len(blocks) - (limit - 1)
    note = {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"…ほか {dropped} ブロックを省略しました（全文はレポートのファイルを見てください）"}],
    }
    return [*blocks[: limit - 1], note]


def slack_blocks(s: Summary, project: str, space_url: str = "") -> tuple[str, list[dict[str, Any]]]:
    """Slack のメッセージ（通知の文と blocks）. section は 3000 字・メッセージは 50 ブロックの上限に収める."""
    esc = mrkdwn_escape

    def link(key: str) -> str:
        return f"<{space_url}/view/{key}|{esc(key)}>" if space_url else esc(key)

    text = f"進捗レポート {esc(project)}（{s.today}）: 完了 {s.done}/{s.total}、期限超過 {len(s.delayed)} 件"
    ms = [
        f"• *{esc(m.name)}* {m.done}/{m.total}（{_pct(m.ratio)}）経過 {_pct(m.expected_ratio(s.today))} {_state(m.ratio, m.expected_ratio(s.today))}"
        for m in s.milestones
    ]
    delayed = [f"• {link(i.key)} {esc(i.summary)}（{esc(i.assignee or '未割当')}、期限 {i.due}）" for i in s.delayed[:SLACK_DELAYED_ROWS]]
    hidden = max(0, len(s.delayed) - SLACK_DELAYED_ROWS)
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": _clip(f"進捗レポート {project}（{s.today}）", SLACK_HEADER_TEXT)}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"完了 *{s.done}/{s.total}* ／ 期限超過 *{len(s.delayed)}* ／ 期限間近 {len(s.due_soon)}"},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": fit_lines("*マイルストーン*", ms)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": fit_lines("*期限超過*", delayed, hidden=hidden)}},
    ]
    return text, limit_blocks(blocks)
