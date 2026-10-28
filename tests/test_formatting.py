from pathlib import Path

from fence.config import parse_config
from fence.git.runner import Repo
from fence.index.formatting import detect_format_only
from fence.index.indexer import index_repo
from fence.index.store import Store
from tests.gitfix import FixtureRepo

SRC = "def f(a,b):\n  return g('x', a)\n"
BLACK = 'def f(a, b):\n    return g("x", a)\n'


def _index(fx: FixtureRepo, tmp_path: Path) -> Store:
    store = Store.open(tmp_path / "i.sqlite3")
    index_repo(Repo.open(fx.root), store, parse_config(""), detect_format=detect_format_only)
    return store


def test_reformat_commit_is_flagged(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": SRC, "b.js": "f( 'x' )\n"}, "add")
    fmt = fx.commit({"a.py": BLACK, "b.js": 'f("x");\n'}, "Apply black and prettier")
    real = fx.commit({"a.py": BLACK.replace("a)", "b)")}, "Pass b")
    store = _index(fx, tmp_path)
    assert store.commit(fmt).format_only
    assert not store.commit(real).format_only


def test_unworded_reindent_with_same_line_count_is_flagged(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.go": "package p\nfunc f() int {\n  return 1\n}\n"}, "add")
    fmt = fx.commit({"a.go": "package p\nfunc f() int {\n\treturn 1\n}\n"}, "tabs")
    assert _index(fx, tmp_path).commit(fmt).format_only


def test_comment_only_change_counts_as_formatting(tmp_path: Path) -> None:
    # Comments carry no behaviour; code.warning_comment evidence is read from the
    # current source, not from this commit.
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "x = 1  # old\n"}, "add")
    c = fx.commit({"a.py": "x = 1  # new\n"}, "comment")
    assert _index(fx, tmp_path).commit(c).format_only


def test_adds_and_binaries_are_never_formatting(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": SRC, "x.bin": b"\x00\x01"}, "add")
    add = fx.commit({"b.py": "y = 1\n"}, "Reformat code: add b")
    binary = fx.commit({"x.bin": b"\x00\x02"}, "reformat binary")
    store = _index(fx, tmp_path)
    assert not store.commit(add).format_only
    assert not store.commit(binary).format_only


def test_read_blobs_skips_missing_and_oversized(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    sha = fx.commit({"a.py": "x\n", "big.py": "y" * 5000}, "add")
    repo = Repo.open(fx.root)
    out = repo.read_blobs([(sha, "a.py"), (sha, "nope.py"), (sha, "big.py")], max_bytes=1000)
    assert out == {(sha, "a.py"): "x\n", (sha, "nope.py"): None, (sha, "big.py"): None}
