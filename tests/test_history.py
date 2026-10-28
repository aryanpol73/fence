from pathlib import Path

import pytest

from fence.git.history import LogFormat, iter_log, parse_stream
from fence.git.runner import GitError, GitTimeout, Repo
from tests.gitfix import FixtureRepo


def _entries(fx: FixtureRepo) -> list:
    repo = Repo.open(fx.root)
    return list(iter_log(repo, [repo.verify_ref("HEAD")]))


def test_parses_add_modify_rename_delete_binary(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "a\nb\nc\nd\n", "bin.dat": b"\x00\x01"}, "first")
    fx.mv("a.py", "pkg/b.py")
    fx.commit({"pkg/b.py": "a\nb\nc\nd\ne\n", "bin.dat": b"\x00\x02"}, "move a (#7)\n\nbody")
    fx.commit({"bin.dat": None}, "drop binary")
    first, second, third = _entries(fx)
    assert [c.status for c in first.changes] == ["A", "A"]
    by_path = {c.path: c for c in second.changes}
    assert by_path["pkg/b.py"].status == "R"
    assert by_path["pkg/b.py"].old_path == "a.py"
    assert (by_path["pkg/b.py"].added, by_path["pkg/b.py"].deleted) == (1, 0)
    assert by_path["bin.dat"].added is None  # binary
    assert second.subject == "move a (#7)" and second.body == "body"
    assert third.changes[0].status == "D"
    assert second.parents == (first.sha,)


def test_message_cannot_forge_record_boundaries(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fake = "\x1eS\x1fF\x1dE deadbeef" * 3
    fx.commit({"a.py": "x\n"}, f"evil\n\n{fake}")
    (e,) = _entries(fx)
    assert fake in e.body


def test_streaming_handles_tiny_chunks(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    for i in range(5):
        fx.commit({f"f{i}.py": f"{i}\n"}, f"c{i} ünïcode")
    repo = Repo.open(fx.root)
    fmt = LogFormat()
    from fence.git.history import log_args

    text = "".join(repo.stream([*log_args(fmt), "--reverse", "HEAD", "--"]))
    chunks = [text[i : i + 7] for i in range(0, len(text), 7)]
    entries = list(parse_stream(chunks, fmt))
    assert [e.subject for e in entries] == [f"c{i} ünïcode" for i in range(5)]


def test_merge_commits_have_no_changes(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "1\n"}, "base")
    fx.git("checkout", "-q", "-b", "feat")
    fx.commit({"b.py": "2\n"}, "feature")
    fx.git("checkout", "-q", "main")
    fx.merge("feat", "Merge pull request #12 from org/feat")
    merge = _entries(fx)[-1]
    assert len(merge.parents) == 2 and merge.changes == []


def test_stream_timeout_and_failure(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "1\n"}, "base")
    repo = Repo.open(fx.root)
    with pytest.raises(GitError):
        list(repo.stream(["log", "no-such-ref"]))
    with pytest.raises(GitTimeout):
        list(repo.stream(["log", "HEAD"], timeout=0.000001))


def test_stream_early_stop_kills_git(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "1\n" * 50_000}, "big")
    repo = Repo.open(fx.root)
    gen = repo.stream(["log", "-p", "HEAD"], chunk_size=16)
    next(gen)
    gen.close()  # must not hang or leak


def test_stream_refuses_string_args(tmp_path: Path) -> None:
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "1\n"}, "base")
    with pytest.raises(TypeError):
        list(Repo.open(fx.root).stream("log"))
