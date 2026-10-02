"""モックの Backlog データ（架空のプロジェクト SAMPLE）を生成する. 基準日 2026-10-02 の時点の状態.

    python samples/make_samples.py     # samples/backlog/ に project.json / versions.json / issues.json を書く

Backlog API v2 の応答と同じ形（使う項目だけ）。人名は「担当A」などの記号。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = date(2026, 10, 2)

STATUS = {"未対応": 1, "処理中": 2, "処理済み": 3, "完了": 4}
# (件名, 種別, 担当, 状態, 開始(基準日からの日数), 期限(同), 見積h, 実績h, マイルストーン, 最終更新(同))
ISSUES = [
    ("要件の確認会", "タスク", "担当A", "完了", -30, -25, 8, 8, "フェーズ1 設計", -25),
    ("会員 API の基本設計", "タスク", "担当A", "完了", -24, -15, 24, 28, "フェーズ1 設計", -14),
    ("注文 API の基本設計", "タスク", "担当B", "完了", -24, -12, 24, 22, "フェーズ1 設計", -12),
    ("注文取込バッチの基本設計", "タスク", "担当C", "完了", -20, -10, 16, 20, "フェーズ1 設計", -9),
    ("画面の基本設計", "タスク", "担当B", "処理済み", -18, -5, 32, 30, "フェーズ1 設計", -3),
    ("設計レビューの指摘対応", "タスク", "担当A", "処理中", -6, -1, 12, 6, "フェーズ1 設計", -1),
    ("非機能要件の整理", "タスク", "担当C", "未対応", -8, -2, 8, 0, "フェーズ1 設計", -8),
    ("会員 API の実装", "タスク", "担当A", "処理中", -3, 6, 32, 10, "フェーズ2 製造", -1),
    ("会員 API の単体テスト", "タスク", "担当A", "未対応", 4, 9, 16, 0, "フェーズ2 製造", -10),
    ("注文 API の実装", "タスク", "担当B", "処理中", -2, 8, 40, 6, "フェーズ2 製造", 0),
    ("注文 API の単体テスト", "タスク", "担当B", "未対応", 7, 12, 16, 0, "フェーズ2 製造", -10),
    ("注文取込バッチの実装", "タスク", "担当C", "未対応", -1, 10, 32, 0, "フェーズ2 製造", -10),
    ("バッチの再実行テスト", "タスク", "担当C", "未対応", 9, 14, 8, 0, "フェーズ2 製造", -10),
    ("ローカル環境の手順書", "タスク", "担当D", "完了", -5, -2, 6, 5, "フェーズ2 製造", -2),
    ("CI のゲート設定", "タスク", "担当D", "処理中", -2, 2, 10, 6, "フェーズ2 製造", 0),
    ("検証環境の構築", "タスク", "担当D", "未対応", 1, 5, 16, 0, "フェーズ2 製造", -10),
    ("ログイン時のエラー表示が崩れる", "バグ", "担当B", "処理中", -2, 1, 2, 1, "フェーズ2 製造", 0),
    ("会員検索で 0 件のときに 500 になる", "バグ", "", "未対応", -1, 3, 3, 0, "フェーズ2 製造", -1),
    ("性能テストの計画", "タスク", "担当C", "未対応", None, None, 8, 0, "フェーズ2 製造", -10),
]


def generate(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)

    def d(n: int | None) -> str | None:
        return None if n is None else (BASE + timedelta(days=n)).isoformat()

    project = {"id": 1001, "projectKey": "SAMPLE", "name": "サンプル開発プロジェクト"}
    versions = [
        {
            "id": 1,
            "projectId": 1001,
            "name": "フェーズ1 設計",
            "startDate": d(-30) + "T00:00:00Z",
            "releaseDueDate": d(-1) + "T00:00:00Z",
            "archived": False,
        },
        {
            "id": 2,
            "projectId": 1001,
            "name": "フェーズ2 製造",
            "startDate": d(-5) + "T00:00:00Z",
            "releaseDueDate": d(15) + "T00:00:00Z",
            "archived": False,
        },
        {
            "id": 3,
            "projectId": 1001,
            "name": "フェーズ0 準備（終了）",
            "startDate": d(-60) + "T00:00:00Z",
            "releaseDueDate": d(-31) + "T00:00:00Z",
            "archived": True,
        },
    ]
    vid = {v["name"]: v["id"] for v in versions}
    issues = []
    for n, (summary, typ, who, status, start, due, est, act, ms, upd) in enumerate(ISSUES, start=1):
        issues.append(
            {
                "id": 50000 + n,
                "issueKey": f"SAMPLE-{n}",
                "summary": summary,
                "issueType": {"name": typ},
                "status": {"id": STATUS[status], "name": status},
                "assignee": {"name": who} if who else None,
                "startDate": None if start is None else d(start) + "T00:00:00Z",
                "dueDate": None if due is None else d(due) + "T00:00:00Z",
                "estimatedHours": est,
                "actualHours": act,
                "milestone": [{"id": vid[ms], "name": ms}],
                "created": d(-35) + "T09:00:00Z",
                "updated": d(upd) + "T18:00:00Z",
                "parentIssueId": None,
            }
        )
    files = {"project.json": project, "versions.json": versions, "issues.json": issues}
    for name, data in files.items():
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return [out / n for n in files]


if __name__ == "__main__":
    for p in generate(HERE / "backlog"):
        print(p)
