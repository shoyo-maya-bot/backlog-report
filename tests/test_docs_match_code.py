"""ドキュメント（README・config.example.yaml・setup・CHANGELOG）と実際のコード・設定のずれを機械で見つける."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml
from devtools_common.cli import ExitCode

from backlog_report.config import load_settings
from backlog_report.entrypoints import cli

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_python_version_matches_requires_python():
    m = re.fullmatch(r">=\s*(\d+)\.(\d+)", _pyproject()["project"]["requires-python"])
    assert m
    v = f"{m.group(1)}.{m.group(2)}"
    assert f"Python {v} 以上" in README
    setup_sh = (ROOT / "setup.sh").read_text(encoding="utf-8")
    assert f"sys.version_info < ({m.group(1)}, {m.group(2)})" in setup_sh and f"Python {v} 以上" in setup_sh
    assert f"Python {v}" in (ROOT / "setup.cmd").read_bytes().decode("cp932")  # setup.cmd は cmd.exe 向けに Shift_JIS
    for wf in (ROOT / ".github/workflows/ci.yml", ROOT / "docs/examples/scheduled-report.yml"):
        assert set(re.findall(r'python-version: "([\d.]+)"', wf.read_text(encoding="utf-8"))) == {v}, wf.name


def test_exit_code_table_matches_constants_and_help():
    section = README.split("## 終了コード", 1)[1].split("\n## ", 1)[0]
    codes = [int(c) for c in re.findall(r"^\| `(\d+)` \|", section, re.M)]
    assert codes == sorted(int(c) for c in ExitCode)
    doc = cli.__doc__ or ""
    for c in codes:
        assert re.search(rf"(終了コード: |/ ){c} ", doc), f"cli の docstring に終了コード {c} がありません"


def _example_keys() -> list[tuple[str, str]]:
    """config.example.yaml の設定項目（コメントアウトしたものも含む）."""
    text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    keys: list[tuple[str, str]] = []
    section = ""
    for line in text.splitlines():
        if m := re.match(r"^(\w+):\s*$", line):
            section = m.group(1)
        elif section and (m := re.match(r"^\s+(?:#\s*)?(\w+):\s", line)):
            keys.append((section, m.group(1)))
    return keys


def test_config_example_keys_are_read_and_documented():
    keys = _example_keys()
    assert ("report", "milestones") in keys and ("report", "today") in keys
    src = (ROOT / "src/backlog_report/config.py").read_text(encoding="utf-8")
    read = set(re.findall(r"""\b[brsw]\.get\("(\w+)"|_str_list\(\w, "\w+", "(\w+)\"""", src))
    read_keys = {a or b for a, b in read} | {"today"}  # today は r.get("today") と r["today"]
    for sec, key in keys:
        assert key in read_keys, f"config.example.yaml の {sec}.{key} をコードが読んでいません"
        assert f"{sec}.{key}" in README or f"`{key}`" in README, f"README に {sec}.{key} の説明がありません"


def test_config_example_loads_with_documented_defaults(tmp_path: Path):
    """config.example.yaml の値は、コードの既定値と同じ（例をコピーしただけで既定と違う動きにならない）."""
    data = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    st = load_settings(p)
    minimal = tmp_path / "m.yaml"
    minimal.write_text(yaml.safe_dump({"backlog": data["backlog"]}, allow_unicode=True), encoding="utf-8")
    default = load_settings(minimal)
    for attr in ("done_statuses", "first_status", "milestones", "due_soon_days", "parent_child", "output_dir", "snapshot_file", "post_log"):
        assert getattr(st, attr) == getattr(default, attr), attr


def test_changelog_pin_matches_pyproject():
    """CHANGELOG の devtools-common の固定の最後の版が、pyproject.toml の固定と同じ."""
    dep = next(d for d in _pyproject()["project"]["dependencies"] if d.startswith("devtools-common"))
    sha = dep.rsplit("@", 1)[1]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    line = next(ln for ln in changelog.splitlines() if "devtools-common の固定を" in ln and "→" in ln)
    last = re.findall(r"`([0-9a-f]{7,40})`", line)[-1]
    assert sha.startswith(last), (last, sha)
