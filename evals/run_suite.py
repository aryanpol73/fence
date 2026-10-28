"""Run the Removal-Regret Backtest over every repo in evals/repos.toml.

    uv run python evals/run_suite.py [--only NAME ...] [--max-commits 500] [--window 300]

Resumable: a repo whose results file already exists is skipped. Each repo is
partial-cloned (blobs over 1 MB are left out) into a cache outside the working
tree and checked out at its pinned SHA. The backtest runs in local-git mode
(GitHub token removed from the environment), so results do not depend on network
or credentials. Per-repo results go to evals/results/<name>.json and
<name>.jsonl (one outcome per removal block).

This script clones repositories, so it starts git itself; Fence's own code
still goes through fence/git/runner.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
CACHE = Path(os.environ.get("FENCE_EVAL_CACHE", Path.home() / ".cache" / "fence-evals"))


def git(*args: str, cwd: Path | None = None, timeout: int = 1800) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def checkout(repo: dict) -> Path:
    dest = CACHE / "repos" / repo["name"]
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "--quiet", "--filter=blob:limit=1m", "--no-tags", repo["url"], str(dest))
    try:
        git("cat-file", "-e", f"{repo['sha']}^{{commit}}", cwd=dest)
    except RuntimeError:
        git("fetch", "--quiet", "origin", repo["sha"], cwd=dest)
    git("checkout", "--quiet", "--detach", repo["sha"], cwd=dest)
    return dest


def run_one(repo: dict, max_commits: int, window: int) -> dict:
    # Imported here so --help works without the package installed.
    from fence.context import Context
    from fence.eval.backtest import backtest
    from fence.index.formatting import detect_format_only
    from fence.index.indexer import index_repo

    path = checkout(repo)
    os.environ["FENCE_CACHE_DIR"] = str(CACHE / "index")
    os.environ.pop("GITHUB_TOKEN", None)  # local-git mode for reproducibility
    ctx = Context.open(path)
    index_repo(ctx.repo, ctx.store, ctx.cfg, detect_format=detect_format_only)
    rep = backtest(ctx, rev=repo["sha"], max_commits=max_commits, window=window)
    with (RESULTS / f"{repo['name']}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for o in rep.outcomes:
            fh.write(json.dumps(o.__dict__, sort_keys=True) + "\n")
    summary = {**rep.summary(), "repo": repo["name"], "url": repo["url"],
               "language": repo["language"], "sha": repo["sha"],
               "max_commits": max_commits, "window": window}
    ctx.close()
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--max-commits", type=int, default=500)
    ap.add_argument("--window", type=int, default=300)
    args = ap.parse_args()
    repos = tomllib.loads((ROOT / "evals" / "repos.toml").read_text(encoding="utf-8"))["repo"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    for repo in repos:
        if args.only and repo["name"] not in args.only:
            continue
        out = RESULTS / f"{repo['name']}.json"
        if out.exists():
            print(f"{repo['name']}: done already, skipping", flush=True)
            continue
        t0 = time.monotonic()
        print(f"{repo['name']}: running", flush=True)
        try:
            summary = run_one(repo, args.max_commits, args.window)
        except Exception as e:  # noqa: BLE001 - report and continue with the next repo
            print(f"{repo['name']}: FAILED {e}", flush=True)
            continue
        out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"{repo['name']}: {summary['removal_blocks']} blocks, "
              f"{summary['regretted_blocks']} regretted, {time.monotonic() - t0:.0f}s",
              flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
