import io
import json
import os
import subprocess
import urllib.error
from email.message import Message
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fence.cli import app
from fence.github.comment import upsert
from fence.render import COMMENT_MARKER

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("FENCE_BYPASS", raising=False)


def test_hook_install_refuses_foreign_hook_and_uninstalls(demo: dict, tmp_path: Path) -> None:
    import shutil

    repo = tmp_path / "copy"
    shutil.copytree(demo["repo"], repo)
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n")
    assert runner.invoke(app, ["hook", "install", "-C", str(repo)]).exit_code == 1
    assert runner.invoke(app, ["hook", "install", "--force", "--pre-push", "-C", str(repo)]
                         ).exit_code == 0
    assert "fence check --staged --hook" in (hooks / "pre-commit").read_text()
    assert runner.invoke(app, ["hook", "uninstall", "--pre-push", "-C", str(repo)]).exit_code == 0
    assert not (hooks / "pre-commit").exists()


def test_hook_mode_blocks_staged_fence_removal_and_fails_open(demo: dict, tmp_path: Path) -> None:
    import shutil

    repo = tmp_path / "copy"
    shutil.copytree(demo["repo"], repo)
    assert runner.invoke(app, ["index", "-C", str(repo), "-q"]).exit_code == 0
    f = repo / "payments" / "client.py"
    f.write_bytes(f.read_bytes().replace(b"            time.sleep(2)\n", b""))
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    blocked = runner.invoke(app, ["check", "--staged", "--hook", "-C", str(repo), "--no-color"])
    assert blocked.exit_code in (2, 3) and "KEEP" in blocked.output
    # No index for an unrelated repo: hook mode must not block or index.
    other = tmp_path / "other"
    other.mkdir()
    assert runner.invoke(app, ["check", "--hook", "-C", str(other)]).exit_code == 0


def test_markdown_report_has_marker_and_ack(demo: dict, tmp_path: Path) -> None:
    out = tmp_path / "r.md"
    res = runner.invoke(app, ["check", "--base", "main", "--head", "cleanup-payments", "-C",
                              demo["repo"], "--format", "markdown", "-o", str(out), "-q"])
    assert res.exit_code in (2, 3)
    md = out.read_text()
    assert md.startswith(COMMENT_MARKER) and "**KEEP**" in md and "fence ack" in md


class _Resp(io.BytesIO):
    pass


def test_sticky_comment_updates_in_place() -> None:
    calls = []
    existing = [{"id": 5, "body": "unrelated"}, {"id": 9, "body": COMMENT_MARKER + " old"}]

    def opener(req, timeout=None):
        calls.append((req.get_method(), req.full_url))
        return _Resp(json.dumps(existing if req.get_method() == "GET" else {}).encode())

    assert upsert("https://api.github.com", "a/b", 3, "new", "t", opener=opener) == "updated"
    assert calls[-1] == ("PATCH", "https://api.github.com/repos/a/b/issues/comments/9")


def test_sticky_comment_creates_and_handles_forks() -> None:
    def opener(req, timeout=None):
        if req.get_method() == "GET":
            return _Resp(b"[]")
        raise urllib.error.HTTPError(req.full_url, 403, "ro", Message(), None)

    assert upsert("https://api.github.com", "a/b", 3, "x", "t", opener=opener) == "forbidden"
    with pytest.raises(ValueError):
        upsert("http://insecure", "a/b", 3, "x", "t", opener=opener)


def test_action_yml_never_uses_pull_request_target() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "action.yml").read_text()
    assert "pull_request_target" not in text and "fence comment" in text
    assert os.path.exists(root / ".github" / "workflows" / "ci.yml")
