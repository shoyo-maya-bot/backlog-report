@echo off
rem セットアップ（Windows）: .venv を作って backlog-report を入れ、設定の雛形をコピーする
rem   依存の版は constraints.txt（CI と同じ）でそろえる
cd /d "%~dp0"
set C=
if exist constraints.txt set C=-c constraints.txt
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul || goto :oldpy
%PY% -m venv .venv || goto :err
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q %C% -e ".[dev,bot]" || goto :err
if not exist config.yaml copy config.example.yaml config.yaml >nul
if not exist .env copy .env.example .env >nul
echo 完了: .venv\Scripts\activate のあと backlog-report -c samples\config.yaml report
pause
exit /b 0
:oldpy
echo Python 3.12 以上が必要です（%PY% で起動できないか、版が古い）。Python 3.12 以上を入れてから実行してください。
pause
exit /b 1
:err
echo セットアップに失敗しました。Python 3.12 以上と Git が入っているか確認してください。
pause
exit /b 1
