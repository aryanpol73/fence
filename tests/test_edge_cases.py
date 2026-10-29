"""`why` on awkward repositories: stale indexes, binary files, shallow clones.

Each case must either be explained correctly or report its uncertainty. None may
present a reason the history does not support.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fence.cli import app
from fence.context import Context
from fence.decisions.build import WhyError, why
from fence.index.indexer import index_repo
from fence.mcp.server import Server
from tests.gitfix import FixtureRepo

runner = CliRunner()


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FixtureRepo:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return FixtureRepo(tmp_path / "r")


def _why_json(root: Path, target: str) -> dict:
    out = runner.invoke(app, ["why", target, "-C", str(root), "--json"])
    assert out.exit_code == 0, out.output
    return json.loads(out.stdout)


def test_commits_after_the_last_index_are_indexed_first(fx: FixtureRepo) -> None:
    fx.commit({"a.py": "X = 1\n"}, "wip")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    # Amending after indexing: without a refresh the new commit is explained from its
    # subject line alone and its stated reason is lost.
    fx.write({"a.py": "X = 2\n"})
    fx.git("add", "-A")
    fx.git("commit", "-q", "--amend", "-m", "Change X\n\nX is 2 because 1 overflowed.")
    (d,) = _why_json(fx.root, "a.py")["decisions"]
    assert d["grade"] == "B" and d["stated_reason"] == "X is 2 because 1 overflowed."
    body = json.loads(Server(str(fx.root)).call("why", {"target": "a.py"})["content"][0]["text"])
    assert body["decisions"][0]["grade"] == "B"


def test_binary_file_is_refused_not_explained(fx: FixtureRepo) -> None:
    fx.commit({"img.bin": b"GIF89a\x00\x01binary"}, "Add image")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    with pytest.raises(WhyError, match="binary"):
        why(ctx, "img.bin")
    out = runner.invoke(app, ["why", "img.bin", "-C", str(fx.root)])
    assert out.exit_code == 1 and "binary" in out.output and "Traceback" not in out.output


def test_shallow_boundary_message_is_not_offered_as_a_reason(fx: FixtureRepo,
                                                             tmp_path: Path) -> None:
    fx.commit({"a.py": "def f():\n    return 1\n"}, "Add f\n\nf returns 1 because callers need it.")
    fx.commit({"b.py": "Y = 1\n", "c.py": "Z = 1\n"},
              "Add Y and Z\n\nThe values are 1 since the API requires it.")
    clone = tmp_path / "shallow"
    fx.git("clone", "-q", "--depth", "1", f"file://{fx.root.as_posix()}", str(clone))
    res = _why_json(clone, "a.py")
    (d,) = res["decisions"]
    # The boundary commit did not add a.py's lines; history before it is cut off.
    assert d["grade"] == "D" and d["change_reason"] is None
    assert any("shallow" in n for n in res["notes"])


def test_mcp_why_carries_the_same_scope_as_json(fx: FixtureRepo) -> None:
    fx.commit({"a.py": "def alpha():\n    return 1\n", "b.py": "def beta():\n    return 2\n"},
              "Split helpers\n\nThe old module was split because imports were circular.")
    (cli,) = _why_json(fx.root, "a.py")["decisions"]
    body = json.loads(Server(str(fx.root)).call("why", {"target": "a.py"})["content"][0]["text"])
    (mcp,) = body["decisions"]
    assert cli["reason_scope"] == mcp["reason_scope"] == "change"
    assert (cli["grade"], cli["reason"]) == (mcp["grade"], mcp["reason"])
    assert mcp["reason"].startswith("Inferred: added in a change to 2")


def test_scores_are_labelled_experimental_everywhere(fx: FixtureRepo) -> None:
    fx.commit({"a.py": "def f(x):\n    if x is None:\n        raise ValueError(x)\n    return x\n"},
              "Guard f\n\nf rejects None because callers corrupted the cache.")
    fx.write({"a.py": "def f(x):\n    return x\n"})
    out = runner.invoke(app, ["check", "-C", str(fx.root), "--json"])
    assert json.loads(out.stdout)["score_note"].startswith("Experimental")
    assert _why_json(fx.root, "a.py:1")["score_note"].startswith("Experimental")
    text = runner.invoke(app, ["check", "-C", str(fx.root)]).output
    assert "not probabilities" in text and "Outcome:" not in text
    s = Server(str(fx.root))
    for tool, args in (("why", {"target": "a.py"}), ("check", {})):
        body = s.call(tool, args)["content"][0]["text"]
        assert '"score_note":"Experimental: Strength' in body
