from pathlib import Path

import pytest

from fence.context import Context
from fence.eval.backtest import backtest
from fence.index.formatting import detect_format_only
from fence.index.indexer import index_repo
from tests.gitfix import FixtureRepo

GUARD = ("def pay(card, amount):\n"
         "    if amount <= 0:\n"
         "        raise ValueError('amount must be positive')\n"
         "    return card.charge(amount)\n")
NO_GUARD = "def pay(card, amount):\n    return card.charge(amount)\n"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def test_regretted_removal_is_labelled_and_warned(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"pay.py": NO_GUARD}, "add pay")
    fx.commit({"pay.py": GUARD}, "Reject non-positive amounts\n\n"
              "Negative amounts caused refunds to be issued as charges during the incident.")
    fx.commit({"other.py": "x = 1\n"}, "unrelated")
    removal = fx.commit({"pay.py": NO_GUARD}, "Simplify pay")
    fx.commit({"other.py": "x = 2\n"}, "unrelated 2")
    fx.commit({"pay.py": GUARD}, "Restore amount check")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg, detect_format=detect_format_only)
    rep = backtest(ctx, max_commits=10, window=3)
    hits = [o for o in rep.outcomes if o.sha == removal]
    assert hits and hits[0].regretted and hits[0].regret_kind == "restored"
    assert hits[0].verdict in ("KEEP", "VERIFY")
    s = rep.summary()
    assert s["recall_warn"] == 1.0 and s["precision_warn"] is not None
    assert s["median_check_seconds"] is not None


def test_no_future_leak_in_scoring(tmp_path: Path) -> None:
    # The revert happens after the removal; when replaying the removal, Fence must not
    # see it (it would add revert points to the decision).
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": NO_GUARD}, "add")
    guard = fx.commit({"a.py": GUARD}, "add guard")
    fx.commit({"a.py": NO_GUARD}, "drop guard")
    fx.git("revert", "--no-edit", "HEAD")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    rep = backtest(ctx, max_commits=10, window=2)
    drop = [o for o in rep.outcomes if o.regret_kind == "reverted"]
    assert drop
    assert ctx.store.reverts_of(guard) == []  # sanity: guard itself was never reverted
