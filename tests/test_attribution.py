"""Attribution accuracy on a labelled scenario set: Fence vs plain `git blame`.

Each scenario builds a real repo where the true originating commit of one line is
known, then asks both tools. Run with -s to see the table; the numbers are
quoted in the README.
"""



import pytest

from fence.context import Context
from fence.decisions.build import why
from fence.index.formatting import detect_format_only
from fence.index.indexer import index_repo
from tests.gitfix import FixtureRepo

GUARD = "    if amount <= 0:\n        raise ValueError('bad amount')\n"


def _pay(indent: str = "    ", q: str = "'") -> str:
    return (f"def pay(card, amount):\n{indent}if amount <= 0:\n{indent}{indent}raise "
            f"ValueError({q}bad amount{q})\n{indent}return card.charge(amount)\n")


def s_rename(fx: FixtureRepo):
    fx.commit({"a.py": "def pay(card, amount):\n    return card.charge(amount)\n"}, "add")
    origin = fx.commit({"a.py": _pay()}, "Reject bad amounts")
    fx.mv("a.py", "billing/pay.py")
    fx.commit({}, "Move module")
    return origin, "billing/pay.py", 2


def s_reformat(fx: FixtureRepo):
    origin = fx.commit({"a.py": _pay("  ")}, "Reject bad amounts")
    fx.commit({"a.py": _pay("    ", '"')}, "Apply black")
    return origin, "a.py", 3


def s_reindent(fx: FixtureRepo):
    origin = fx.commit({"a.py": _pay("  ")}, "Reject bad amounts")
    fx.commit({"a.py": _pay("    ")}, "Use 4 spaces")
    return origin, "a.py", 2


def s_cross_file_move(fx: FixtureRepo):
    other = "def helper():\n    return 1\n"
    origin = fx.commit({"a.py": _pay() + "\n\n" + other, "b.py": "x = 1\n"}, "Reject bad amounts")
    fx.commit({"a.py": other, "b.py": "x = 1\n\n\n" + _pay()}, "Move pay to b")
    return origin, "b.py", 5


def s_extract_method(fx: FixtureRepo):
    body = ("def pay(card, amount):\n    log('pay')\n" + GUARD +
            "    return card.charge(amount)\n")
    origin = fx.commit({"a.py": body}, "Reject bad amounts")
    new = ("def _check(amount):\n" + GUARD.replace("    ", "    ", 1) +
           "\n\ndef pay(card, amount):\n    log('pay')\n    _check(amount)\n"
           "    return card.charge(amount)\n")
    fx.commit({"a.py": new}, "Extract _check")
    return origin, "a.py", 2


def s_unrelated_edit_above(fx: FixtureRepo):
    origin = fx.commit({"a.py": _pay()}, "Reject bad amounts")
    fx.commit({"a.py": "import os\n\n" + _pay()}, "Import os")
    return origin, "a.py", 4


SCENARIOS = [s_rename, s_reformat, s_reindent, s_cross_file_move, s_extract_method,
             s_unrelated_edit_above]


def _plain_blame(fx: FixtureRepo, path: str, line: int) -> str:
    out = fx.git("blame", "--porcelain", "-L", f"{line},{line}", "HEAD", "--", path)
    return out.split()[0]


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> list[tuple[str, bool, bool]]:
    import os

    rows = []
    for s in SCENARIOS:
        d = tmp_path_factory.mktemp(s.__name__)
        os.environ["FENCE_CACHE_DIR"] = str(d / "cache")
        fx = FixtureRepo(d / "r")
        origin, path, line = s(fx)
        ctx = Context.open(fx.root)
        index_repo(ctx.repo, ctx.store, ctx.cfg, detect_format=detect_format_only)
        fence_sha = why(ctx, path, line).decisions[0].introduced_sha
        rows.append((s.__name__, fence_sha == origin, _plain_blame(fx, path, line) == origin))
        ctx.close()
    return rows


def test_fence_beats_plain_blame(results: list[tuple[str, bool, bool]]) -> None:
    fence = sum(r[1] for r in results)
    plain = sum(r[2] for r in results)
    print(f"\nattribution: fence {fence}/{len(results)}, plain git blame {plain}/{len(results)}")
    for name, f, p in results:
        print(f"  {name:<24} fence={'ok' if f else 'MISS'} blame={'ok' if p else 'MISS'}")
    assert fence >= plain
    assert fence == len(results)
