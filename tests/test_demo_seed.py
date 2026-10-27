import importlib.util
import json
from pathlib import Path

import pytest

SEED_PATH = Path(__file__).resolve().parents[1] / "demo" / "seed_demo_repo.py"
_spec = importlib.util.spec_from_file_location("seed_demo_repo", SEED_PATH)
seed_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_mod)


def test_cleanup_diff_removes_the_sleep_and_import(tmp_path: Path) -> None:
    seed_mod.seed(tmp_path / "out")
    diff = (tmp_path / "out" / "cleanup.diff").read_text()
    removed = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert removed == ["-import time", "-            time.sleep(2)"]


def test_seed_is_reproducible(tmp_path: Path) -> None:
    a = seed_mod.seed(tmp_path / "a")
    b = seed_mod.seed(tmp_path / "b")
    for key in ("initial", "fence", "format", "ignore_revs", "refund", "cleanup", "harmless"):
        assert a[key] == b[key]
    assert json.loads((tmp_path / "a" / "shas.json").read_text())["fence"] == a["fence"]


def test_no_ignore_revs_variant(tmp_path: Path) -> None:
    shas = seed_mod.seed(tmp_path / "out", ignore_revs=False)
    assert "ignore_revs" not in shas
    assert not (Path(shas["repo"]) / ".git-blame-ignore-revs").exists()


def test_refuses_to_replace_foreign_directory(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "precious.txt").write_text("keep me")
    with pytest.raises(SystemExit):
        seed_mod.seed(out, force=True)
    assert (out / "precious.txt").exists()
