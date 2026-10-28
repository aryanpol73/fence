from pathlib import Path

import pytest

from fence.config import parse_config
from fence.git.runner import Repo
from fence.index.indexer import index_repo, pr_from_subject, reverted_sha
from fence.index.store import Store
from tests.gitfix import FixtureRepo


@pytest.fixture
def fx(tmp_path: Path) -> FixtureRepo:
    f = FixtureRepo(tmp_path / "r")
    f.commit({"app.py": "def f():\n    return 1\n"}, "initial")
    return f


def _index(fx: FixtureRepo, store: Store, toml: str = ""):
    return index_repo(Repo.open(fx.root), store, parse_config(toml))


def test_incremental_runs_only_index_new_commits(fx: FixtureRepo, tmp_path: Path) -> None:
    store = Store.open(tmp_path / "i.sqlite3")
    assert _index(fx, store).indexed == 1
    again = _index(fx, store)
    assert (again.indexed, again.skipped_existing) == (0, 0)
    fx.commit({"app.py": "def f():\n    return 2\n"}, "fix: return 2")
    third = _index(fx, store)
    assert third.indexed == 1 and third.total == 2


def test_force_push_marks_orphans(fx: FixtureRepo, tmp_path: Path) -> None:
    store = Store.open(tmp_path / "i.sqlite3")
    doomed = fx.commit({"app.py": "x = 1\n"}, "will be rewritten")
    _index(fx, store)
    fx.git("reset", "-q", "--hard", "HEAD~1")
    fx.commit({"app.py": "x = 2\n"}, "replacement")
    report = _index(fx, store)
    assert report.rewrite_detected and report.orphaned == 1
    assert store.commit(doomed).orphaned
    assert report.total == 2


def test_squash_and_merge_subjects_map_to_prs(fx: FixtureRepo, tmp_path: Path) -> None:
    squash = fx.commit({"a.py": "1\n"}, "Add retry (#482)")
    fx.git("checkout", "-q", "-b", "feat")
    fx.commit({"b.py": "2\n"}, "feature work")
    fx.git("checkout", "-q", "main")
    merge = fx.merge("feat", "Merge pull request #12 from org/feat")
    store = Store.open(tmp_path / "i.sqlite3")
    _index(fx, store)
    assert (store.commit(squash).pr_number, store.commit(squash).pr_source) == (
        482, "squash-subject")
    assert store.commit(merge).pr_number == 12
    # The squash PR number is not double-counted as an issue reference.
    assert ("text.issue_ref", "#482") not in store.signals_for(squash)


def test_revert_links_and_signals(fx: FixtureRepo, tmp_path: Path) -> None:
    fix = fx.commit(
        {"app.py": "def f():\n    return None\n"}, "Fix crash on empty input\n\nFixes #9"
    )
    fx.git("revert", "--no-edit", fix)
    revert = fx.head()
    store = Store.open(tmp_path / "i.sqlite3")
    _index(fx, store)
    assert store.reverted_by(revert) == fix
    assert store.reverts_of(fix) == [revert]
    sigs = store.signals_for(fix)
    assert ("text.closes_issue", "#9") in sigs and ("text.fix", "Fix") in sigs


def test_exclude_globs_and_bulk_flag(fx: FixtureRepo, tmp_path: Path) -> None:
    many = {f"gen/f{i}.py": f"{i}\n" for i in range(5)}
    bulk = fx.commit({**many, "vendor/lib.py": "v\n"}, "big drop")
    store = Store.open(tmp_path / "i.sqlite3")
    _index(fx, store, "[index]\nbulk_commit_files = 5\n")
    paths = [c.path for c in store.changes_for(bulk)]
    assert "vendor/lib.py" not in paths and len(paths) == 5
    assert store.commit(bulk).bulk and store.commit(bulk).files_changed == 6


def test_history_depth_limits_commits(fx: FixtureRepo, tmp_path: Path) -> None:
    for i in range(4):
        fx.commit({"app.py": f"v{i}\n"}, f"c{i}")
    store = Store.open(tmp_path / "i.sqlite3")
    assert _index(fx, store, "[index]\nhistory_depth = 2\n").indexed == 2


def test_empty_repo_is_reported_not_crashed(tmp_path: Path) -> None:
    empty = FixtureRepo(tmp_path / "e")
    report = _index(empty, Store.open(tmp_path / "i.sqlite3"))
    assert report.empty and report.indexed == 0


def test_shallow_clone_reports_reduced_coverage(fx: FixtureRepo, tmp_path: Path) -> None:
    for i in range(3):
        fx.commit({"app.py": f"v{i}\n"}, f"c{i}")
    clone = tmp_path / "shallow"
    fx.git("clone", "-q", "--depth", "2", f"file://{fx.root.as_posix()}", str(clone))
    report = index_repo(Repo.open(clone), Store.open(tmp_path / "s.sqlite3"), parse_config(""))
    assert report.shallow and report.indexed == 2 and "shallow" in report.notes[0]


def test_detached_head(fx: FixtureRepo, tmp_path: Path) -> None:
    fx.commit({"app.py": "v2\n"}, "second")
    fx.git("checkout", "-q", "--detach", "HEAD~1")
    assert _index(fx, Store.open(tmp_path / "i.sqlite3")).indexed == 1


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Merge pull request #5 from a/b", (5, "merge-subject")),
        ("Fix thing (#77)", (77, "squash-subject")),
        ("Fix thing #77 later", (None, None)),
        ("Merge branch 'x'", (None, None)),
    ],
)
def test_pr_from_subject(subject: str, expected: tuple) -> None:
    assert pr_from_subject(subject) == expected


def test_reverted_sha() -> None:
    assert reverted_sha("This reverts commit " + "a" * 40 + ".") == "a" * 40
    assert reverted_sha("This reverts commit abc.") is None
