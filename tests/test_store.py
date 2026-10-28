from pathlib import Path

import pytest

from fence.index.store import (
    SCHEMA_VERSION,
    CommitRow,
    FileChange,
    IndexUnavailable,
    Store,
    db_path_for,
)


def _row(sha: str, subject: str = "s") -> CommitRow:
    return CommitRow(sha, (), "A", "a@x", 1, 1, subject, "")


def test_migrates_and_reopens(tmp_path: Path) -> None:
    p = tmp_path / "i.sqlite3"
    s = Store.open(p)
    assert s.get_meta("schema_version") == str(SCHEMA_VERSION)
    with s.transaction():
        s.put_commits([_row("a" * 40)], [])
    s.close()
    s2 = Store.open(p)
    assert s2.has_commit("a" * 40)
    assert s2.integrity_ok()


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "i.sqlite3"
    s = Store.open(p)
    with s.transaction():
        s.set_meta("schema_version", str(SCHEMA_VERSION + 1))
    s.close()
    with pytest.raises(IndexUnavailable, match="newer"):
        Store.open(p)


def test_corrupt_file_gives_actionable_error(tmp_path: Path) -> None:
    p = tmp_path / "i.sqlite3"
    p.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(IndexUnavailable, match="reindex"):
        Store.open(p)


def test_missing_index_without_create(tmp_path: Path) -> None:
    with pytest.raises(IndexUnavailable, match="fence index"):
        Store.open(tmp_path / "nope.sqlite3", create=False)


def test_transaction_rolls_back(tmp_path: Path) -> None:
    s = Store.open(tmp_path / "i.sqlite3")
    with pytest.raises(RuntimeError), s.transaction():
        s.put_commits([_row("b" * 40)], [])
        raise RuntimeError("boom")
    assert not s.has_commit("b" * 40)


def test_path_history_follows_renames(tmp_path: Path) -> None:
    s = Store.open(tmp_path / "i.sqlite3")
    with s.transaction():
        s.put_commits(
            [_row("1" * 40), _row("2" * 40)],
            [
                FileChange("1" * 40, "b.py", "a.py", "R", 90, 0, 0),
                FileChange("2" * 40, "c.py", "b.py", "R", 90, 0, 0),
            ],
        )
    assert s.path_history("c.py") == ["c.py", "b.py", "a.py"]


def test_orphans_marked_and_unmarked(tmp_path: Path) -> None:
    s = Store.open(tmp_path / "i.sqlite3")
    with s.transaction():
        s.put_commits([_row("1" * 40), _row("2" * 40)], [])
        assert s.mark_orphans(["1" * 40]) == 1
    assert s.commit("2" * 40).orphaned
    assert s.commit_count() == 1
    with s.transaction():
        s.mark_orphans(["1" * 40, "2" * 40])
    assert not s.commit("2" * 40).orphaned


def test_db_path_is_stable_and_outside_repo(tmp_path: Path) -> None:
    a = db_path_for(tmp_path / "cache", tmp_path / "repo")
    assert a == db_path_for(tmp_path / "cache", tmp_path / "repo")
    assert (tmp_path / "cache") in a.parents
