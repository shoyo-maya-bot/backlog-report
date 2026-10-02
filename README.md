# backlog-report

Backlog の課題から **進捗レポート** を作り、Slack と Backlog Wiki に届けるツールです。

- **わかること**: マイルストーンごとの完了率と経過（順調 / やや遅れ / 遅れ）、期限超過・期限間近・開始日を過ぎて未着手の課題、担当者別の負荷
- **グラフ**: ガントチャート（状態で色分け・今日の線）とバーンダウン（理想と実績）
- **届け方**: Markdown ファイル、Slack（Bot トークンなら画像も）、Backlog Wiki のページを上書き
- **動かし方**: その場で確認（report）／ 定期実行（batch）／ Slack ボット（`/progress`）

構成（6 層）と動かし方の違いは [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) を参照してください。

## すぐ使う（モックで試す）

API キーが無くても、同梱の架空のデータ（`samples/backlog/`）で試せます。

1. clone する
2. **Windows**: `setup.cmd` をダブルクリック ／ **macOS・Linux**: `./setup.sh`
3. 実行する

```bash
backlog-report -c samples/config.yaml report                       # 画面に Markdown
backlog-report -c samples/config.yaml report --milestone "フェーズ2 製造"
backlog-report -c samples/config.yaml batch --wiki                 # output/ にレポートとグラフ、Wiki はモックのフォルダへ
```

## 本番の Backlog で使う

1. `config.yaml`（setup がコピー済み）の `backlog.space_url` と `project_key` を自分のプロジェクトに直す
2. `.env` に `BACKLOG_API_KEY` を書く（Backlog の 個人設定 → API で発行。**.env はコミットしない**）
3. `backlog-report report` で確認する

| 設定（config.yaml） | 意味 |
|---|---|
| `report.done_statuses` | 完了とみなす状態（既定: 完了。処理済みも含めるなら `[処理済み, 完了]`） |
| `report.milestones` | 対象のマイルストーン（空ならアーカイブ以外のすべて） |
| `report.due_soon_days` | 何日以内を「期限間近」とするか（既定 3） |
| `slack.channel` | Bot トークンで投稿するチャンネル ID |
| `wiki.page` | 上書きする Wiki のページ名（手で編集しても次回の実行で上書きされる） |

API キー・トークンを config.yaml に書くとエラーで止まります（誤ってコミットしないため）。

## Slack

| 方法 | .env | できること |
|---|---|---|
| Incoming Webhook | `SLACK_WEBHOOK_URL` | 文章の投稿のみ |
| Bot トークン | `SLACK_BOT_TOKEN`（chat:write, files:write） | 投稿とグラフの画像 |
| ボット（Socket Mode） | 上に加え `SLACK_APP_TOKEN`（connections:write）と、スラッシュコマンド `/progress` の登録 | `/progress`、`/progress マイルストーン名`、`/progress @担当者名` |

Slack アプリの作成とトークンの発行は、Slack のワークスペースの管理者のルールに従ってください（無料プランでも使えます）。

## 定期実行

`docs/examples/scheduled-report.yml` が GitHub Actions の例です（毎週月曜の朝に Slack と Wiki へ）。
Windows のタスクスケジューラなら `backlog-report batch --slack --wiki` を登録します。
バーンダウンの実績は実行のたびに `data/snapshots.csv` に記録されるので、毎日実行すると正確になります。

## 終了コード

`0` 正常 ／ `1` 期限超過あり（`--fail-on-delay` のとき）／ `2` 設定・引数の誤り（API キーが無い など）／ `3` 実行時エラー（API・Slack の失敗）

## 開発

```bash
./setup.sh                    # Windows: setup.cmd
ruff check . && ruff format --check . && pytest
```

テストはモックと偽の HTTP で動き、Backlog・Slack には接続しません。
