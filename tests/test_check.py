from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fence.check.classify import classify
from fence.check.diff import Block, DiffError, DiffSpec, parse_diff
from fence.check.engine import CLEAR, EXIT_OK, KEEP, VERIFY, check, effective_policy
from fence.config import parse_config
from fence.context import Context
from fence.index.formatting import detect_format_only
from fence.index.indexer import index_repo
from fence.policy import acks
from tests.gitfix import FixtureRepo


@pytest.fixture(autouse=True)
def _cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("FENCE_BYPASS", raising=False)


def _ctx(root: Path) -> Context:
    ctx = Context.open(root)
    index_repo(ctx.repo, ctx.store, ctx.cfg, detect_format=detect_format_only)
    return ctx


def test_demo_cleanup_is_stopped_with_evidence(demo: dict) -> None:
    ctx = _ctx(Path(demo["repo"]))
    res = check(ctx, DiffSpec(base="main", head="cleanup-payments"))
    assert res.verdict == KEEP
    f = res.findings[0]
    assert f.decision.introduced_sha == demo["fence"]
    assert f.kind in ("delete", "loosen")
    assert "time.sleep(2)" in f.removed_preview
    assert f.decision.stated_reason.endswith("produced duplicate charges.")
    assert res.exit_code in (2, 3)
    assert any(x.id == "risk.path" for x in f.risk.factors)  # payments/ is a sensitive path


def test_demo_harmless_rename_is_clear(demo: dict) -> None:
    ctx = _ctx(Path(demo["repo"]))
    res = check(ctx, DiffSpec(base="main", head="harmless-rename"))
    assert res.exit_code == EXIT_OK
    assert all(f.verdict != KEEP for f in res.findings)


def test_diff_text_input_matches_git_diff(demo: dict) -> None:
    ctx = _ctx(Path(demo["repo"]))
    text = (Path(demo["out"]) / "cleanup.diff").read_text()
    res = check(ctx, diff_text=text, base="main")
    assert res.verdict == KEEP


def test_ack_clears_the_block_and_is_invalidated_by_code_change(demo: dict) -> None:
    ctx = _ctx(Path(demo["repo"]))
    res = check(ctx, DiffSpec(base="main", head="cleanup-payments"))
    f = res.findings[0]
    ledger = ctx.repo.root / ctx.cfg.policy.ack_ledger
    for g in res.findings:  # acks are per block
        acks.append(ledger, decision_id=g.decision.id, path=g.path,
                    fingerprint=g.decision_fingerprint,
                    reason="Gateway now dedupes by idempotency key", who="t@example.com",
                    expiry_days=30)
    try:
        again = check(ctx, DiffSpec(base="main", head="cleanup-payments"))
        assert again.exit_code == EXIT_OK and again.findings[0].ack is not None
        loaded, problems = acks.load(ledger)
        assert problems == []
        # Different code under the same decision: the ack no longer applies.
        assert acks.find_valid(loaded, f.decision.id, "other-fingerprint") is None
        # Expired.
        later = datetime.now(UTC) + timedelta(days=31)
        assert acks.find_valid(loaded, f.decision.id, f.decision_fingerprint, later) is None
    finally:
        ledger.unlink()


def test_ack_requires_a_real_reason(tmp_path: Path) -> None:
    with pytest.raises(acks.AckError):
        acks.append(tmp_path / "a.jsonl", decision_id="D1", path="a", fingerprint="f",
                    reason="ok", who="x", expiry_days=None)


def test_malformed_ledger_lines_are_reported_not_fatal(tmp_path: Path) -> None:
    ledger = tmp_path / "acks.jsonl"
    ledger.write_text('not json\n{"decision_id": "D1"}\n')
    loaded, problems = acks.load(ledger)
    assert loaded == [] and len(problems) == 2


def test_bypass_env_exits_zero(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(Path(demo["repo"]))
    monkeypatch.setenv("FENCE_BYPASS", "1")
    res = check(ctx, DiffSpec(base="main", head="cleanup-payments"))
    assert res.bypassed and res.exit_code == EXIT_OK and res.verdict == KEEP


def test_reason_unknown_never_blocks(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    src = ("import threading\n_lock = threading.Lock()\n\ndef put(d, k, v):\n"
           "    with _lock:\n        if k is None:\n            raise ValueError(k)\n"
           "        d[k] = v\n")
    fx.commit({"auth/store.py": src}, "wip")
    fx.git("checkout", "-q", "-b", "cleanup")
    fx.commit({"auth/store.py": "def put(d, k, v):\n    d[k] = v\n"}, "simplify")
    res = check(_ctx(fx.root), DiffSpec(base="main", head="cleanup"))
    assert res.exit_code == EXIT_OK
    assert all(f.decision.grade == "D" and f.verdict != KEEP for f in res.findings)


def test_working_tree_and_staged_changes(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"pay.py": "def charge():\n    retry(times=3)\n    return 1\n"},
              "Retry charges 3 times\n\nOne attempt caused failed payments during the outage.")
    (fx.root / "pay.py").write_text("def charge():\n    return 1\n")
    ctx = _ctx(fx.root)
    work = check(ctx, DiffSpec(base="HEAD"))
    assert work.findings and work.findings[0].decision.grade == "B"
    assert check(ctx, DiffSpec(base="HEAD", staged=True)).findings == []
    fx.git("add", "pay.py")
    assert check(ctx, DiffSpec(base="HEAD", staged=True)).findings


def test_preimage_mismatch_is_an_error(demo: dict) -> None:
    ctx = _ctx(Path(demo["repo"]))
    text = (Path(demo["out"]) / "cleanup.diff").read_text().replace(
        "-            time.sleep(2)", "-            time.sleep(9)")
    with pytest.raises(DiffError, match="does not match"):
        check(ctx, diff_text=text, base="main")


def test_ignore_paths(demo: dict) -> None:
    ctx = _ctx(Path(demo["repo"]))
    ctx.cfg = parse_config('[policy]\nignore_paths = ["payments/**"]\n')
    res = check(ctx, DiffSpec(base="main", head="cleanup-payments"))
    assert res.findings == [] and res.files_checked == 0


def test_path_override_strictest_wins() -> None:
    cfg = parse_config("")
    assert effective_policy(cfg, "src/payments/x.py")[:3] == ("medium", "critical", 10)
    assert effective_policy(cfg, "src/ui/x.py")[:3] == ("high", "critical", 0)


# classification ---------------------------------------------------------------

def _b(removed: list[str], added: list[str]) -> Block:
    return Block([(i + 1, t) for i, t in enumerate(removed)],
                 [(i + 1, t) for i, t in enumerate(added)])


def test_classify_delete_loosen_alter() -> None:
    empty: frozenset[int] = frozenset()
    assert classify("a.py", _b(["    time.sleep(2)"], []), empty).kind == "delete"
    loosen = classify("a.py", _b(["    if user is None:", "        raise Denied()"],
                                 ["    pass"]), empty)
    assert loosen.kind in ("loosen", "delete")
    assert classify("a.py", _b(["    retries = 5  # retry budget"],
                               ["    retries = 1  # retry budget"]), empty).kind == "loosen"
    assert classify("a.py", _b(["    total = a + b"], ["    total = a + b + c"]),
                    empty).kind == "alter"


def test_classify_move_keeps_guard_and_detects_dropped_guard() -> None:
    from fence.anchors.fingerprint import shingles
    from fence.lang import tokens_for

    body = ["def charge(gw, card):", "    if card is None:", "        raise ValueError(card)",
            "    return gw.charge(card)"]
    moved = frozenset(shingles(tokens_for("b.py", "\n".join(body))))
    assert classify("a.py", _b(body, []), moved).kind == "move"
    without_guard = [body[0], body[3]]
    dropped = frozenset(shingles(tokens_for("b.py", "\n".join(without_guard))))
    assert classify("a.py", _b(body, []), dropped).kind != "move"


def test_parse_diff_rename_and_delete() -> None:
    text = (
        "diff --git a/a.py b/b.py\nsimilarity index 80%\nrename from a.py\nrename to b.py\n"
        "--- a/a.py\n+++ b/b.py\n@@ -1,3 +1,3 @@\n x\n-y\n+z\n w\n"
        "diff --git a/c.py b/c.py\ndeleted file mode 100644\n--- a/c.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-q\n-r\n"
    )
    a, c = parse_diff(text)
    assert (a.old_path, a.new_path, a.status) == ("a.py", "b.py", "renamed")
    assert a.blocks[0].removed == [(2, "y")] and a.blocks[0].added == [(2, "z")]
    assert (c.status, c.new_path, len(c.blocks[0].removed)) == ("deleted", None, 2)


def test_parse_diff_rejects_garbage() -> None:
    assert parse_diff("not a diff") == []
    assert CLEAR and VERIFY  # verdict names are exported
