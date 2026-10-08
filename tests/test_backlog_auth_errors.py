"""Backlog の認証・プロジェクトキーの誤り（401/403/404）は設定の誤り（終了コード 2）. 通信・5xx は実行時エラー（3）のまま."""

from __future__ import annotations

import contextlib
import io
import urllib.error
from pathlib import Path

import pytest
from devtools_common.cli import run_main

from backlog_report.entrypoints.cli import main
from backlog_report.infrastructure.backlog import BacklogApi


def _opener(status: int):
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, status, "error", {}, None)  # type: ignore[arg-type]

    return opener


def run_with_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int) -> tuple[int, str]:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("backlog: {project_key: NOPE, space_url: 'https://example.backlog.com'}\n", encoding="utf-8")
    api = BacklogApi("https://example.backlog.com", "NOPE", "k", opener=_opener(status), sleep=lambda _s: None)
    monkeypatch.setattr("backlog_report.entrypoints.cli.make_port", lambda st, base: api)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(main, ["--env", "/nonexistent/.env", "-c", str(cfg), "report"])
    return rc, out.getvalue() + err.getvalue()


@pytest.mark.parametrize(("status", "hint"), [(401, "BACKLOG_API_KEY"), (403, "権限"), (404, "project_key")])
def test_auth_and_project_errors_are_config_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, hint: str):
    rc, out = run_with_status(tmp_path, monkeypatch, status)
    assert rc == 2, out
    assert hint in out and str(status) in out


def test_server_error_stays_runtime_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rc, out = run_with_status(tmp_path, monkeypatch, 503)
    assert rc == 3, out
