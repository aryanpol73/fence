from pathlib import Path

import pytest

from fence.check.diff import DiffSpec
from fence.check.engine import SAFE, check
from fence.context import Context
from fence.decisions.build import why
from fence.decisions.obsolete import manifests
from fence.git.runner import Repo
from fence.index.indexer import index_repo
from tests.gitfix import FixtureRepo


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def _ctx(fx: FixtureRepo) -> Context:
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    return ctx


def test_dependency_upgrade_marks_workaround_stale(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"requirements.txt": "urllib3==1.24\n", "net.py": "def get():\n    return 1\n"},
              "init")
    fx.commit({"net.py": "def get():\n    strip_header()  # urllib3 bug\n    return 1\n"},
              "Workaround for urllib3 header bug\n\nStrip the header because urllib3 1.24 "
              "sends it twice.")
    fx.commit({"requirements.txt": "urllib3==2.0\n"}, "Upgrade urllib3")
    (d,) = why(_ctx(fx), "net.py", 2).decisions
    assert d.status == "stale-suspected"
    assert any(e.type == "obsolescence" and "upgraded from 1.24 to 2.0" in e.quote
               for e in d.evidence)
    # Removing a stale fence with a stated reason is a SAFE-TO-REMOVE candidate, not KEEP.
    fx.git("checkout", "-q", "-b", "drop")
    fx.commit({"net.py": "def get():\n    return 1\n"}, "Drop workaround")
    res = check(_ctx(fx), DiffSpec(base="main", head="drop"))
    assert res.findings and res.findings[0].verdict == SAFE and res.exit_code == 0


def test_removed_flag_and_unused_private_function(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "def _legacy_path():\n    if flags.old_checkout:\n        return 1\n",
               "b.py": "x = 1\n"}, "add legacy path")
    (d,) = why(_ctx(fx), "a.py", 2).decisions
    quotes = " ".join(e.quote for e in d.evidence if e.type == "obsolescence")
    assert "old_checkout" in quotes and "_legacy_path" in quotes


def test_public_functions_are_never_called_unused(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"api.py": "def public_entry():\n    return 1\n"}, "add api")
    (d,) = why(_ctx(fx), "api.py", 2).decisions
    assert d.status == "active"


def test_manifest_parsing(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"package.json": '{"dependencies": {"left-pad": "^1.3.0"}}',
               "go.mod": "module x\nrequire github.com/spf13/pflag v1.0.5\n",
               "pyproject.toml": '[project]\ndependencies = ["requests>=2.31"]\n'}, "m")
    deps = manifests(Repo.open(fx.root), "HEAD")
    assert deps["left-pad"] == "1.3.0" and deps["github.com/spf13/pflag"] == "1.0.5"
    assert deps["requests"] == "2.31"
