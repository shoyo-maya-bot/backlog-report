@echo off
rem セットアップ（Windows）: .venv を作って backlog-report を入れ、設定の雛形をコピーする
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m venv .venv || goto :err
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -e ".[dev,bot]" || goto :err
if not exist config.yaml copy config.example.yaml config.yaml >nul
if not exist .env copy .env.example .env >nul
echo 完了: .venv\Scripts\activate のあと backlog-report -c samples\config.yaml report
pause
exit /b 0
:err
echo セットアップに失敗しました。Python 3.12 と Git が入っているか確認してください。
pause
exit /b 1
