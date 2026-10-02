"""backlog-report — Backlog の課題から進捗レポート（遅延・担当者別・ガント・バーンダウン）を作り、Slack と Wiki に届ける."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("backlog-report")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
