# 構成（6 層）

依存は上から下への一方向です。ドメイン層は Backlog・Slack・ファイル形式を知りません。

| 層 | フォルダ | 役割 | 主な中身 |
|---|---|---|---|
| 1 入口 | `entrypoints/` | 起動のしかたごとの入口 | `cli.py`（report = その場で確認 / batch = 定期実行）、`slack_bot.py`（Socket Mode の /progress） |
| 2 アプリケーション | `application/` | ユースケースの手順 | レポートを作る・ファイルに書く・残量を記録する・Slack に送る・Wiki を更新する |
| 3 ドメイン | `domain/` | 進捗の計算（純粋な関数） | 期限超過・期限間近・未着手・マイルストーンの進捗・担当者別の負荷・バーンダウン |
| 4 インフラ | `infrastructure/` | 外部とのやりとり | Backlog API（本物とモック）、Slack（Bot トークン / Webhook）、残量の記録（CSV） |
| 5 表示 | `rendering/` | 人が読む形にする | Markdown・Backlog Wiki・Slack のブロック、ガントチャートとバーンダウン（PNG） |
| 6 設定 | `config.py` | 設定と秘密情報 | config.yaml（秘密情報を書くとエラー）と .env |

```text
entrypoints ──▶ application ──▶ domain
                    │
                    ├──▶ infrastructure ──▶ (Backlog API / Slack / CSV)
                    └──▶ rendering
config ◀── すべての層が参照
```

## 3 つの動かし方

| モード | コマンド | 使いどころ |
|---|---|---|
| online（その場で確認） | `backlog-report report [--milestone] [--assignee]` | 朝会の前に状況を見る。Markdown を画面に出すだけで、何も送らない |
| batch（定期実行） | `backlog-report batch [--slack] [--wiki]` | 週次の報告。ファイル出力・残量の記録・Slack 投稿・Wiki 更新。例: `docs/examples/scheduled-report.yml` |
| slack-bot | `backlog-report bot` | Slack で `/progress マイルストーン名` と打つと、その場でレポートを返す |

## 完了日とバーンダウン

Backlog の課題には「完了した日」の項目がありません。次の順で実績を求めます。

1. `batch` を実行するたびに、マイルストーンごとの残り（件数と見積時間）を `data/snapshots.csv` に記録する。記録がある日はそれを使う
2. 記録が無い日は「完了状態の課題の最終更新日」を完了日とみなす（完了後に課題を更新すると、完了日が後ろにずれる）

正確なバーンダウンが必要なら、毎日 `batch` を実行して記録をためてください。記録がない日があると `batch` が警告を出します
（マイルストーンの開始日から昨日まで。今日の分はその実行で記録する）。GitHub Actions では記録を `report-data` ブランチに残します（README の「定期実行」）。
親課題と子課題は `report.parent_child` に従って二重に数えません（完了率と同じ）。見積時間が 1 件でも入っているマイルストーンは見積時間で、
それ以外は件数で描きます。

## モック

`backlog.mock_dir` を設定すると、API の代わりに JSON（`project.json` / `versions.json` / `issues.json`）を読みます。
Wiki の更新は `<mock_dir>/wiki/` にファイルとして書きます。API の応答と同じ形なので、本物の応答を保存して試すこともできます
（API キーを含む URL やログは保存しないでください）。
