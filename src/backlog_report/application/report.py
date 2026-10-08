"""ユースケース: レポートを作る・Slack に送る・Wiki を更新する・残量を記録する. 層 2: アプリケーション."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..config import Settings, local_today
from ..domain.model import Issue, Milestone
from ..domain.progress import Summary, burndown, countable, is_done, snapshot_gaps, summarize
from ..infrastructure import posts, snapshots
from ..infrastructure.backlog import BacklogPort, load_all
from ..infrastructure.slack import SlackClient
from ..rendering import charts, text


@dataclass
class Report:
    summary: Summary
    project: str
    issues: list[Issue]
    milestones: list[Milestone]
    markdown: str = ""
    files: dict[str, Path] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _slug(s: str) -> str:
    return re.sub(r"[^\w.-]+", "_", s).strip("_") or "milestone"


def build(
    port: BacklogPort,
    st: Settings,
    *,
    milestone: str = "",
    assignee: str = "",
    today: date | None = None,
    now: datetime | None = None,
) -> Report:
    """レポートを作る. 今日は today → 設定の today → 設定のタイムゾーンでの現在の日付（now. 既定は現在時刻）の順に決める."""
    today = today or st.today or local_today(st.timezone, now)
    issues, milestones = load_all(port, st.timezone)
    targets = [milestone] if milestone else st.milestones
    if targets:
        unknown = [m for m in targets if m not in {x.name for x in milestones}]
        if unknown:
            raise ValueError(f"マイルストーンがありません: {', '.join(unknown)}")
        milestones = [m for m in milestones if m.name in targets]
        issues = [i for i in issues if set(i.milestones) & set(targets)]
    else:
        milestones = [m for m in milestones if not m.archived]  # 終了（アーカイブ）したものは指定したときだけ
    if assignee:
        issues = [i for i in issues if i.assignee == assignee]
    s = summarize(issues, milestones, today, st.done_statuses, st.due_soon_days, st.first_status, st.parent_child)
    return Report(s, st.project_key, issues, milestones)


def write_files(r: Report, st: Settings, out_dir: Path, *, with_charts: bool = True) -> Report:
    out_dir.mkdir(parents=True, exist_ok=True)
    chart_links: dict[str, str] = {}
    if with_charts:
        g = charts.gantt(
            r.issues,
            r.summary.today,
            st.done_statuses,
            out_dir / "gantt.png",
            f"ガントチャート（{r.project}）",
            first_status=st.first_status,
        )
        if g:
            r.files["gantt"] = g
            chart_links["ガントチャート"] = g.name
        snap = Path(st.snapshot_file)
        for m in r.milestones:
            unit = _burndown_unit(r, m, st, snap)
            history = snapshots.load(snap, m.name, unit)
            gaps = snapshot_gaps(history, m, r.summary.today)
            if gaps:
                r.warnings.append(_gap_message(m.name, gaps))
            pts = burndown(r.issues, m, r.summary.today, st.done_statuses, unit=unit, snapshots=history, parent_child=st.parent_child)
            b = charts.burndown(
                pts,
                out_dir / f"burndown_{_slug(m.name)}.png",
                f"バーンダウン {m.name}",
                "残り見積時間（h）" if unit == "hours" else "残り件数",
            )
            if b:
                r.files[f"burndown:{m.name}"] = b
                chart_links[f"バーンダウン {m.name}"] = b.name
    r.markdown = text.markdown(r.summary, r.project, space_url=st.space_url, charts=chart_links)
    md = out_dir / f"report_{r.summary.today}.md"
    md.write_text(r.markdown, encoding="utf-8", newline="\n")
    r.files["markdown"] = md
    return r


def _natural_unit(r: Report, m: Milestone, st: Settings) -> str:
    """マイルストーンに見積時間のある課題があれば hours、無ければ count（その日のバーンダウンの単位）."""
    return "hours" if any(i.estimated_hours for i in r.issues if m.name in i.milestones) else "count"


def _burndown_unit(r: Report, m: Milestone, st: Settings, snap: Path) -> str:
    """バーンダウンの単位. 期間内の記録の単位（件数／見積時間）が今日の単位と混ざるときは、警告して件数で描く.

    件数は毎日同じ数え方で記録しているが、見積時間は見積を入れる前の日は 0 なので、時間で描くと線が壊れる。
    """
    unit = _natural_unit(r, m, st)
    start = m.start or date.min
    recorded = {d: u for d, u in snapshots.load_units(snap, m.name).items() if start <= d < r.summary.today and u}
    others = sorted(d for d, u in recorded.items() if u != unit)
    if not others:
        return unit
    days = {u: sum(1 for v in recorded.values() if v == u) for u in snapshots.UNITS}
    first, last = others[0], others[-1]
    span = str(first) if first == last else f"{first}〜{last}"
    r.warnings.append(
        f"バーンダウン {m.name}: 期間内の記録の単位が混ざっています（件数 {days['count']} 日・見積時間 {days['hours']} 日、"
        f"今日は{'見積時間' if unit == 'hours' else '件数'}。今日と違う日: {span}）。件数で描きます"
        "（見積時間で描くには、期間の初めから見積時間を入れてください）"
    )
    return "count"


def _gap_message(name: str, gaps: list[tuple[date, date]]) -> str:
    days = sum((b - a).days + 1 for a, b in gaps)
    spans = "、".join(str(a) if a == b else f"{a}〜{b}" for a, b in gaps[:5]) + ("、…" if len(gaps) > 5 else "")
    return (
        f"バーンダウン {name}: 記録がない日が {days} 日（{spans}）。その日の実績は課題の最終更新日からの推定です"
        "（記録ファイルが消えていないか、毎日実行できているか確認してください）"
    )


def record_snapshot(r: Report, st: Settings) -> None:
    rows = []
    for m in r.milestones:
        group = [i for i in countable(r.issues, st.parent_child) if m.name in i.milestones]
        open_ = [i for i in group if not is_done(i, st.done_statuses)]
        rows.append((m.name, len(open_), sum(i.estimated_hours or 0 for i in open_), _natural_unit(r, m, st)))
    if rows:
        snapshots.record(Path(st.snapshot_file), r.summary.today, rows)


def post_slack(r: Report, st: Settings, client: SlackClient, *, force: bool = False) -> list[str]:
    """Slack に本文とグラフを送る. 送れたものは 1 件ずつ st.post_log に記録し、同じ日・同じ送り先・同じプロジェクトには二度送らない.

    途中（画像のアップロードなど）で失敗して再実行しても、送れていた本文や画像は送り直さない。force で記録を無視して送る。
    戻り値は、送り済みのため飛ばしたもの（message / file:<名前>）。
    """
    if not st.slack_channel and client.bot_token:
        raise ValueError("slack.channel を設定してください（Bot トークンで投稿するとき）")
    log = Path(st.post_log) if st.post_log else None
    done = posts.load(log) if log and not force else set()
    skipped: list[str] = []

    def once(item: str, send) -> None:
        k = posts.key(r.summary.today, st.slack_channel, r.project, item)
        if k in done:
            skipped.append(item)
            return
        send()
        if log:
            posts.add(log, k)  # 送れたらすぐ記録する（次の送信で落ちても、ここまでは送り直さない）

    msg, blocks = text.slack_blocks(r.summary, r.project, st.space_url)
    once("message", lambda: client.post(st.slack_channel, msg, blocks))
    if client.bot_token:  # Webhook では画像を送れない（記録もしない）
        for key, path in r.files.items():
            if key != "markdown":
                once(f"file:{path.name}", lambda path=path: client.upload(st.slack_channel, path, path.stem))
    return skipped


def update_wiki(r: Report, st: Settings, port: BacklogPort) -> str:
    if not st.wiki_page:
        raise ValueError("wiki.page を設定してください")
    port.save_wiki(st.wiki_page, text.wiki(r.summary, r.project, st.space_url))
    return st.wiki_page
