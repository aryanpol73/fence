import importlib.util
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _analyze():
    spec = importlib.util.spec_from_file_location("analyze", ROOT / "evals" / "analyze.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wilson_interval_known_values() -> None:
    a = _analyze()
    assert a.wilson(0, 0) is None
    lo, hi = a.wilson(10, 100)
    assert abs(lo - 0.0552) < 0.001 and abs(hi - 0.1744) < 0.001  # standard Wilson 95%
    lo, hi = a.wilson(0, 50)
    assert lo == 0.0 and 0.06 < hi < 0.08


def test_lift_and_loo_on_synthetic_repos() -> None:
    a = _analyze()

    def rows(n_hi, k_hi, n_lo, k_lo):
        hi = [{"risk": 70, "regretted": i < k_hi, "verdict": "VERIFY", "sha": f"h{i}"}
              for i in range(n_hi)]
        lo = [{"risk": 5, "regretted": i < k_lo, "verdict": "CLEAR", "sha": f"l{i}"}
              for i in range(n_lo)]
        return hi + lo

    per_repo = {"a": rows(40, 8, 200, 4), "b": rows(40, 6, 200, 3), "c": rows(40, 7, 200, 5)}
    assert a.lift(per_repo["a"]) > 3
    for x in a.loo(per_repo):
        assert x["top_bin_above_bottom"] is True


def test_repos_toml_pins_full_shas() -> None:
    repos = tomllib.loads((ROOT / "evals" / "repos.toml").read_text())["repo"]
    assert len(repos) >= 11
    for r in repos:
        assert len(r["sha"]) == 40 and r["url"].startswith("https://")
        assert r["language"] in ("python", "javascript", "go", "java")
