import subprocess
from pathlib import Path

import pytest


def git(cwd: Path, *args: str) -> str:
    """Test-only helper for building fixture repos."""
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "app.py").write_text("def f():\n    return 1\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


def load_seed_module():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "demo" / "seed_demo_repo.py"
    spec = importlib.util.spec_from_file_location("seed_demo_repo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def demo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """The seeded fence-demo repo, with C3 listed in .git-blame-ignore-revs."""
    out = tmp_path_factory.mktemp("demo")
    return {**load_seed_module().seed(out), "out": str(out)}


@pytest.fixture(scope="session")
def demo_no_ignore(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Same history, but the formatter commit is NOT listed in .git-blame-ignore-revs."""
    out = tmp_path_factory.mktemp("demo_no_ignore")
    return {**load_seed_module().seed(out, ignore_revs=False), "out": str(out)}
