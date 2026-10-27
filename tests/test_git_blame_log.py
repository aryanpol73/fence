from pathlib import Path

import pytest

from fence.git.blame import blame, parse_line_porcelain, read_ignore_revs
from fence.git.log import log_line_range, parse_log
from fence.git.runner import GitError, Repo, UnsafeInput
from tests.conftest import git


def _commit(repo: Path, text: str, msg: str, path: str = "app.py") -> str:
    (repo / path).write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def history(tmp_repo: Path) -> dict[str, str]:
    """app.py: initial -> c2 adds a line -> c3 changes a line (to be ignored)."""
    shas = {"initial": git(tmp_repo, "rev-parse", "HEAD").strip()}
    shas["c2"] = _commit(tmp_repo, "def f():\n    return 1\n\nX = 2\n", "add X")
    shas["c3"] = _commit(tmp_repo, "def f():\n    return 10\n\nX = 2\n", "change return")
    return shas


def test_blame_basic_fields(tmp_repo: Path, history: dict[str, str]) -> None:
    lines = blame(Repo.open(tmp_repo), "HEAD", "app.py", [(4, 4)])
    assert len(lines) == 1
    bl = lines[0]
    assert (bl.sha, bl.final_line, bl.content) == (history["c2"], 4, "X = 2")
    assert bl.summary == "add X" and bl.author == "Test" and bl.author_mail == "test@example.com"
    assert bl.previous_sha == history["initial"] and bl.previous_path == "app.py"
    assert not bl.boundary and not bl.ignored


def test_root_commit_has_no_previous(tmp_repo: Path, history: dict[str, str]) -> None:
    (bl,) = blame(Repo.open(tmp_repo), "HEAD", "app.py", [(1, 1)])
    assert bl.sha == history["initial"]
    assert bl.previous_sha is None and bl.previous_path is None


def test_multiple_ranges_one_call(tmp_repo: Path, history: dict[str, str]) -> None:
    lines = blame(Repo.open(tmp_repo), "HEAD", "app.py", [(1, 2), (4, 4)])
    assert [bl.final_line for bl in lines] == [1, 2, 4]


def test_ignore_revs_read_from_base_not_working_tree(
    tmp_repo: Path, history: dict[str, str]
) -> None:
    repo = Repo.open(tmp_repo)
    base = _commit(tmp_repo, history["c3"] + "\n", "ignore c3", ".git-blame-ignore-revs")
    (bl,) = blame(repo, base, "app.py", [(2, 2)])
    assert bl.sha == history["initial"], "c3 is listed at base, so blame skips it"

    # A PR head that rewrites the file in the working tree must not change the result.
    (tmp_repo / ".git-blame-ignore-revs").write_text("")
    git(tmp_repo, "config", "blame.ignoreRevsFile", ".git-blame-ignore-revs")
    (bl,) = blame(repo, base, "app.py", [(2, 2)])
    assert bl.sha == history["initial"]


def test_working_tree_ignore_revs_cannot_hide_a_commit(
    tmp_repo: Path, history: dict[str, str]
) -> None:
    # Attacker: base has no ignore file, but the working tree lists c3 and repo config
    # points git at it. Fence must still blame c3.
    (tmp_repo / ".git-blame-ignore-revs").write_text(history["c3"] + "\n")
    git(tmp_repo, "config", "blame.ignoreRevsFile", ".git-blame-ignore-revs")
    (bl,) = blame(Repo.open(tmp_repo), history["c3"], "app.py", [(2, 2)])
    assert bl.sha == history["c3"]


def test_read_ignore_revs_filters_untrusted_entries(tmp_repo: Path) -> None:
    full = "a" * 40
    text = f"# comment\n{full}\nabc123\nHEAD\n{full}  # dup\n{'B' * 40}\n--output=x\n"
    base = _commit(tmp_repo, text, "ignore file", ".git-blame-ignore-revs")
    assert read_ignore_revs(Repo.open(tmp_repo), base) == [full, "b" * 40]


def test_read_ignore_revs_missing_file(tmp_repo: Path) -> None:
    repo = Repo.open(tmp_repo)
    assert read_ignore_revs(repo, repo.verify_ref("HEAD")) == []


def test_ignored_flag_when_git_cannot_move_past_ignored_commit(tmp_repo: Path) -> None:
    # git 2.43 porcelain has no marker for this case (checked, including with
    # blame.markUnblamableLines=true): a line *added* by an ignored commit is still
    # attributed to it. Fence derives `ignored=True` so callers know to look further.
    added = _commit(tmp_repo, "def f():\n    return 1\nNEW = 1\n", "add NEW")
    base = _commit(tmp_repo, added + "\n", "ignore it", ".git-blame-ignore-revs")
    (bl,) = blame(Repo.open(tmp_repo), base, "app.py", [(3, 3)])
    assert bl.sha == added and bl.ignored


def test_bad_ref_rejected(tmp_repo: Path) -> None:
    with pytest.raises(UnsafeInput):
        blame(Repo.open(tmp_repo), "--output=/tmp/x", "app.py", [(1, 1)])


def test_path_traversal_rejected(tmp_repo: Path) -> None:
    with pytest.raises(UnsafeInput):
        blame(Repo.open(tmp_repo), "HEAD", "../etc/passwd", [(1, 1)])


@pytest.mark.parametrize("ranges", [[], [(0, 1)], [(3, 2)], [(1, 1)] * 201])
def test_bad_ranges_rejected(tmp_repo: Path, ranges) -> None:
    with pytest.raises(UnsafeInput):
        blame(Repo.open(tmp_repo), "HEAD", "app.py", ranges)


def test_range_past_end_of_file_is_git_error(tmp_repo: Path) -> None:
    with pytest.raises(GitError):
        blame(Repo.open(tmp_repo), "HEAD", "app.py", [(50, 51)])


SAMPLE = (
    f"{'a' * 40} 3 3 1\n"
    "author Rahul\nauthor-mail <r@x>\nauthor-time 100\nauthor-tz +0530\n"
    "committer Rahul\ncommitter-time 100\nsummary why\n"
    f"previous {'b' * 40} p y.py\nsome-future-key value\nfilename p y.py\n"
    "\tcontent\x0bwith vertical tab\n"
)


def test_parse_line_porcelain() -> None:
    (bl,) = parse_line_porcelain(SAMPLE)
    assert bl.previous_path == "p y.py" and bl.filename == "p y.py"
    assert bl.content == "content\x0bwith vertical tab"  # not split on \x0b
    assert bl.author_mail == "r@x" and bl.author_tz == "+0530" and bl.author_time == 100


def test_parse_boundary_flag() -> None:
    (bl,) = parse_line_porcelain(SAMPLE.replace("filename", "boundary\nfilename"))
    assert bl.boundary


def test_parse_truncated_output() -> None:
    with pytest.raises(GitError, match="truncated"):
        parse_line_porcelain(SAMPLE.rsplit("\t", 1)[0])


def test_parse_garbage_header() -> None:
    with pytest.raises(GitError):
        parse_line_porcelain("not a header\n\tx\n")


def test_parse_missing_required_field() -> None:
    with pytest.raises(GitError, match="author-time"):
        parse_line_porcelain(SAMPLE.replace("author-time 100\n", ""))


def test_blame_count_mismatch_detected(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo.open(tmp_repo)
    real_run = Repo.run

    def fake_run(self, args, **kw):
        # Simulates git exiting 0 with nothing on stdout (e.g. output cut off).
        return "" if args[0] == "blame" else real_run(self, args, **kw)

    monkeypatch.setattr(Repo, "run", fake_run)
    with pytest.raises(GitError, match="expected 1"):
        blame(repo, "HEAD", "app.py", [(1, 1)])


def test_log_line_range(tmp_repo: Path, history: dict[str, str]) -> None:
    commits = log_line_range(Repo.open(tmp_repo), "HEAD", "app.py", 2, 2)
    assert [c.sha for c in commits] == [history["c3"], history["initial"]]
    assert commits[0].subject == "change return" and commits[0].author == "Test"
    assert commits[0].date.startswith("20")


def test_log_max_count(tmp_repo: Path, history: dict[str, str]) -> None:
    commits = log_line_range(Repo.open(tmp_repo), "HEAD", "app.py", 2, 2, max_count=1)
    assert [c.sha for c in commits] == [history["c3"]]


def test_log_rejects_bad_input(tmp_repo: Path) -> None:
    repo = Repo.open(tmp_repo)
    with pytest.raises(UnsafeInput):
        log_line_range(repo, "HEAD", "../x.py", 1, 1)
    with pytest.raises(UnsafeInput):
        log_line_range(repo, "HEAD", "app.py", 1, 1, max_count=0)


def test_parse_log_rejects_malformed() -> None:
    with pytest.raises(GitError):
        parse_log("deadbeef\x1fonly two\x1e")
