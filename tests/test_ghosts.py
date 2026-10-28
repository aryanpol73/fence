from pathlib import Path

import pytest

from fence.check.diff import DiffSpec
from fence.check.engine import check
from fence.context import Context
from fence.index.ghosts import scan_tombstones
from fence.index.indexer import index_repo
from tests.gitfix import FixtureRepo

GUARD = ("def pay(card, amount):\n    if amount <= 0:\n"
         "        raise ValueError('amount must be positive')\n    return card.charge(amount)\n")
PLAIN = "def pay(card, amount):\n    return card.charge(amount)\n"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))


def test_deleted_guard_leaves_tombstone_and_its_return_is_linked(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"pay.py": GUARD}, "add pay with guard")
    removed = fx.commit({"pay.py": PLAIN}, "Simplify pay")
    fx.commit({"other.py": "x = 1\n"}, "unrelated")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    assert scan_tombstones(ctx.repo, ctx.store, ctx.cfg) == 1
    assert scan_tombstones(ctx.repo, ctx.store, ctx.cfg) == 0  # incremental
    (fx.root / "pay.py").write_text(GUARD)
    res = check(ctx, DiffSpec(base="HEAD"))
    assert res.ghosts and res.ghosts[0][1].removed_in == removed


def test_moves_and_non_guards_leave_no_tombstone(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": GUARD, "b.py": "total = price + tax + shipping + handling\n"}, "add")
    fx.commit({"a.py": None, "c.py": GUARD, "b.py": "total = 0\n"}, "move pay, simplify total")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    assert scan_tombstones(ctx.repo, ctx.store, ctx.cfg) == 0
