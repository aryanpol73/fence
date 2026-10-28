import os
import subprocess
from pathlib import Path

import pytest

from fence.git import runner
from fence.git.runner import GitError, GitTimeout, Repo, UnsafeInput


def test_open_finds_root_from_subdir(tmp_repo: Path) -> None:
    (tmp_repo / "pkg").mkdir()
    repo = Repo.open(tmp_repo / "pkg")
    assert repo.root == tmp_repo.resolve()


def test_verify_ref_resolves_head(tmp_repo: Path) -> None:
    sha = Repo.open(tmp_repo).verify_ref("HEAD")
    assert len(sha) == 40


@pytest.mark.parametrize(
    "ref",
    ["", "--output=/tmp/x", "-p", "HEAD..main", "HEAD main", "HEAD\nmain", "a;rm -rf /", "$(id)"],
)
def test_verify_ref_rejects_unsafe_syntax(tmp_repo: Path, ref: str) -> None:
    with pytest.raises(UnsafeInput):
        Repo.open(tmp_repo).verify_ref(ref)


def test_verify_ref_unknown_ref_is_git_error(tmp_repo: Path) -> None:
    with pytest.raises(GitError):
        Repo.open(tmp_repo).verify_ref("no-such-branch")


@pytest.mark.parametrize("path", ["../outside.py", "pkg/../../outside.py", "/etc/passwd", "-x"])
def test_safe_relpath_rejects_traversal(tmp_repo: Path, path: str) -> None:
    with pytest.raises(UnsafeInput):
        Repo.open(tmp_repo).safe_relpath(path)


def _symlink(target: Path, link: Path) -> None:
    # Windows needs Developer Mode or admin rights to create symlinks.
    try:
        os.symlink(target, link)
    except OSError as e:
        pytest.skip(f"symlinks unavailable here: {e}")


def test_safe_relpath_rejects_symlink_escape(tmp_repo: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    _symlink(secret, tmp_repo / "link.py")
    with pytest.raises(UnsafeInput):
        Repo.open(tmp_repo).safe_relpath("link.py")


def test_safe_relpath_allows_symlink_inside_repo(tmp_repo: Path) -> None:
    _symlink(tmp_repo / "app.py", tmp_repo / "alias.py")
    assert Repo.open(tmp_repo).safe_relpath("alias.py") == "app.py"


def test_safe_relpath_allows_deleted_file(tmp_repo: Path) -> None:
    # A file removed by a diff no longer exists on disk but is still a valid target.
    assert Repo.open(tmp_repo).safe_relpath("gone/old.py") == "gone/old.py"


def test_read_blob(tmp_repo: Path) -> None:
    assert Repo.open(tmp_repo).read_blob("HEAD", "app.py") == "def f():\n    return 1\n"


def test_read_blob_size_cap(tmp_repo: Path) -> None:
    with pytest.raises(GitError):
        Repo.open(tmp_repo).read_blob("HEAD", "app.py", max_bytes=3)


def test_string_args_refused(tmp_repo: Path) -> None:
    with pytest.raises(TypeError):
        Repo.open(tmp_repo).run("status")  # type: ignore[arg-type]


def test_timeout_raises_git_timeout(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repo.open(tmp_repo)

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "run", hang)
    with pytest.raises(GitTimeout):
        repo.run(["log"])


def test_calls_git_with_list_and_no_shell(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repo.open(tmp_repo)
    seen = {}
    real_run = subprocess.run

    def spy(argv, **kwargs):
        seen["argv"], seen["shell"], seen["timeout"] = argv, kwargs.get("shell"), kwargs["timeout"]
        return real_run(argv, **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", spy)
    repo.run(["rev-parse", "HEAD"])
    assert isinstance(seen["argv"], list) and seen["argv"][0] == "git"
    assert seen["shell"] is False
    assert seen["timeout"] == repo.timeout


def test_git_failure_is_git_error(tmp_repo: Path) -> None:
    with pytest.raises(GitError, match="failed"):
        Repo.open(tmp_repo).run(["show", "HEAD:missing.py"])
