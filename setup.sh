#!/usr/bin/env bash
# セットアップ（macOS / Linux / Git Bash）: .venv を作って backlog-report を入れ、設定の雛形をコピーする
set -euo pipefail
cd "$(dirname "$0")"
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
  echo "Python 3.12 以上が必要です（python3 は $(python3 --version 2>&1 || echo '見つかりません')）" >&2
  exit 1
fi
python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip || true
.venv/bin/python -m pip install -q -e ".[dev,bot]"
[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -f .env ] || cp .env.example .env
echo "完了: source .venv/bin/activate のあと backlog-report -c samples/config.yaml report（モックで試す）"
