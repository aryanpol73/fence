"""Compare reason detection between two versions of fence/decisions/reasons.py.

Reads every commit message from the eval indexes (built by `evals/run_suite.py`),
runs `stated_reason` from the current code and from an older copy, and prints the
counts plus every message whose verdict changed, for hand labelling. Offline and fast.

    git show <rev>:fence/decisions/reasons.py > /tmp/reasons_old.py
    uv run python evals/reason_markers.py /tmp/reasons_old.py
"""

from __future__ import annotations

import importlib.util
import os
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("FENCE_EVAL_CACHE", Path.home() / ".cache" / "fence-evals"))


def _load(path: str):
    spec = importlib.util.spec_from_file_location("reasons_old", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from fence.decisions import reasons as new

    old = _load(sys.argv[1])
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    total = before = after = 0
    gained: list[tuple[str, str]] = []
    lost: list[tuple[str, str]] = []
    for db in sorted((CACHE / "index").rglob("*.sqlite3")):
        repo = db.name.split("-")[0]
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        for subject, body in conn.execute("SELECT subject, body FROM commits"):
            msg = f"{subject}\n\n{body}".strip() if body else subject
            total += 1
            a, b = old.stated_reason(msg), new.stated_reason(msg)
            before += a is not None
            after += b is not None
            if b and not a:
                gained.append((repo, b))
            elif a and not b:
                lost.append((repo, a))
        conn.close()
    print(f"messages {total}: stated reason before {before}, after {after}; "
          f"gained {len(gained)}, lost {len(lost)}")
    rng = random.Random(7)  # noqa: S311 - a reproducible sample, not security
    for repo, q in rng.sample(gained, min(sample, len(gained))):
        print(f"+ [{repo}] {q}")
    for repo, q in lost:
        print(f"- [{repo}] {q}")


if __name__ == "__main__":
    main()
