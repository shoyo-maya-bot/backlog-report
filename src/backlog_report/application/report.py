"""ユースケース: レポートを作る・Slack に送る・Wiki を更新する・残量を記録する. 層 2: アプリケーション."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..config import Settings
from ..domain.model import Issue, Milestone
from ..domain.progress import Summary, burndown, is_done, summarize
from ..infrastructure import snapshots
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


def _slug(s: str) -> str:
    return re.sub(r"[^\w.-]+", "_", s).strip("_") or "milestone"


def build(port: BacklogPort, st: Settings, *, milestone: str = "", assignee: str = "", today: date | None = None) -> Report:
    today = today or st.today or date.today()
    issues, milestones = load_all(port)
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
    first = "未対応"
    s = summarize(issues, milestones, today, st.done_statuses, st.due_soon_days, first)
    return Report(s, st.project_key, issues, milestones)


def write_files(r: Report, st: Settings, out_dir: Path, *, with_charts: bool = True) -> Report:
    out_dir.mkdir(parents=True, exist_ok=True)
    chart_links: dict[str, str] = {}
    if with_charts:
        g = charts.gantt(r.issues, r.summary.today, st.done_statuses, out_dir / "gantt.png", f"ガントチャート（{r.project}）")
        if g:
            r.files["gantt"] = g
            chart_links["ガントチャート"] = g.name
        snap = Path(st.snapshot_file)
        for m in r.milestones:
            unit = "hours" if any(i.estimated_hours for i in r.issues if m.name in i.milestones) else "count"
            pts = burndown(r.issues, m, r.summary.today, st.done_statuses, unit=unit, snapshots=snapshots.load(snap, m.name, unit))
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


def record_snapshot(r: Report, st: Settings) -> None:
    rows = []
    for m in r.milestones:
        group = [i for i in r.issues if m.name in i.milestones]
        open_ = [i for i in group if not is_done(i, st.done_statuses)]
        rows.append((m.name, len(open_), sum(i.estimated_hours or 0 for i in open_)))
    if rows:
        snapshots.record(Path(st.snapshot_file), r.summary.today, rows)


def post_slack(r: Report, st: Settings, client: SlackClient) -> None:
    if not st.slack_channel and client.bot_token:
        raise ValueError("slack.channel を設定してください（Bot トークンで投稿するとき）")
    msg, blocks = text.slack_blocks(r.summary, r.project, st.space_url)
    client.post(st.slack_channel, msg, blocks)
    for key, path in r.files.items():
        if key != "markdown":
            client.upload(st.slack_channel, path, path.stem)


def update_wiki(r: Report, st: Settings, port: BacklogPort) -> str:
    if not st.wiki_page:
        raise ValueError("wiki.page を設定してください")
    port.save_wiki(st.wiki_page, text.wiki(r.summary, r.project, st.space_url))
    return st.wiki_page
