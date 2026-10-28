import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fence import __version__
from fence.cli import app, parse_target

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("FENCE_BYPASS", raising=False)


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize(
    ("t", "expected"),
    [("a.py:3", ("a.py", 3, None)), ("a.py:3-9", ("a.py", 3, 9)), ("a.py", ("a.py", None, None)),
     ("C:\\x\\a.py:4", ("C:\\x\\a.py", 4, None))],
)
def test_parse_target(t: str, expected: tuple) -> None:
    assert parse_target(t) == expected


def test_why_json_schema_and_check_exit_codes(demo: dict) -> None:
    r = demo["repo"]
    out = runner.invoke(app, ["why", "payments/client.py:22", "-C", r, "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.stdout)
    assert data["schema"] == "fence/v1" and data["kind"] == "why"
    d = data["decisions"][0]
    assert {"id", "grade", "stated_reason", "strength", "evidence", "reason"} <= set(d)
    assert d["strength"]["top_factors"]

    bad = runner.invoke(app, ["check", "--base", "main", "--head", "cleanup-payments", "-C", r,
                              "--json"])
    assert bad.exit_code in (2, 3)
    cj = json.loads(bad.stdout)
    assert cj["verdict"] == "KEEP" and cj["findings"][0]["ack_command"].startswith("fence ack")

    ok = runner.invoke(app, ["check", "--base", "main", "--head", "harmless-rename", "-C", r, "-q"])
    assert ok.exit_code == 0


def test_ack_command_round_trip(demo: dict, tmp_path: Path) -> None:
    import shutil

    repo = tmp_path / "copy"
    shutil.copytree(demo["repo"], repo)
    first = json.loads(runner.invoke(app, ["check", "--base", "main", "--head",
                                           "cleanup-payments", "-C", str(repo), "--json"]).stdout)
    for f in first["findings"]:  # acks are per block
        span = f"{f['start']}-{f['end']}"
        res = runner.invoke(app, ["ack", f["decision"]["id"], "--at", f"{f['path']}:{span}",
                                  "--rev", "main", "--reason",
                                  "Gateway now dedupes by idempotency key", "-C", str(repo)])
        assert res.exit_code == 0, res.output
    again = runner.invoke(app, ["check", "--base", "main", "--head", "cleanup-payments", "-C",
                                str(repo), "-q"])
    assert again.exit_code == 0
    wrong = runner.invoke(app, ["ack", "Dnope", "--at", "payments/client.py:22", "--reason",
                                "some long reason", "-C", str(repo)])
    assert wrong.exit_code == 1 and "does not cover" in wrong.output


def test_errors_are_actionable_not_tracebacks(demo: dict, tmp_path: Path) -> None:
    res = runner.invoke(app, ["why", "nope.py:1", "-C", demo["repo"]])
    assert res.exit_code == 1 and "cannot read" in res.output
    res = runner.invoke(app, ["why", "a.py:1", "-C", str(tmp_path)])
    assert res.exit_code == 1 and "git repository" in res.output


def test_doctor_and_config(demo: dict) -> None:
    doc = runner.invoke(app, ["doctor", "-C", demo["repo"], "--json"])
    assert doc.exit_code == 0
    names = {c["name"] for c in json.loads(doc.stdout)["checks"]}
    assert {"git", "index", "github"} <= names
    cfg = runner.invoke(app, ["config", "-C", demo["repo"], "--json"])
    assert json.loads(cfg.stdout)["policy"]["fail_on_band"] == "high"


def test_scan_and_report(demo: dict, tmp_path: Path) -> None:
    scan = runner.invoke(app, ["scan", "payments", "-C", demo["repo"], "--json"])
    assert scan.exit_code == 0
    assert json.loads(scan.stdout)["decisions"][0]["file"] == "payments/client.py"
    out = tmp_path / "r.html"
    rep = runner.invoke(app, ["report", "-C", demo["repo"], "--format", "html", "-o", str(out)])
    assert rep.exit_code == 0 and "<h2>Load-bearing" in out.read_text()
    md = runner.invoke(app, ["report", "-C", demo["repo"]])
    assert "Fragile and undocumented" in md.stdout
