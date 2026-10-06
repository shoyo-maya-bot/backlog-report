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
| `report.parent_child` | 完了率で親課題・子課題をどう数えるか（下の表。既定 `children`） |
| `slack.channel` | Bot トークンで投稿するチャンネル ID |
| `wiki.page` | 上書きする Wiki のページ名（手で編集しても次回の実行で上書きされる） |

API キー・トークンを config.yaml に書くとエラーで止まります（誤ってコミットしないため）。

親課題と子課題を両方数えると、同じ作業を二重に数えて完了率がずれます。`report.parent_child` で数え方を選べます。

| `parent_child` | 完了率に数える課題 | 例: 親 1 件＋子 3 件（子 2 件が完了） |
|---|---|---|
| `children`（既定） | 子課題がある親課題は数えず、子課題で数える | 3 件中 2 件（67%） |
| `parents` | 親課題がある子課題は数えず、親課題で数える | 1 件中 0 件（0%） |
| `both` | すべて数える（以前の動き） | 4 件中 2 件（50%） |

対象（マイルストーン・担当者で絞った結果）の中に子課題がない親課題や、親課題がない子課題は、どの設定でも 1 件として数えます。
期限超過・担当者別の一覧とバーンダウンは、この設定に関係なくすべての課題で作ります。

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

## セキュリティ・ライセンス

- 脆弱性を見つけたら、公開の Issue ではなく [SECURITY.md](SECURITY.md) の方法で知らせてください
- ライセンスは [MIT](LICENSE) です
