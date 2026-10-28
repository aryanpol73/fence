"""Pool evals/results/*.jsonl into one evaluation, with 95% intervals.

    uv run python evals/analyze.py            # writes evals/results/pooled.{json,md}

- Proportions get Wilson score intervals.
- Lift (precision / base rate) gets a cluster bootstrap over repos: repos differ, so
  resampling blocks would understate the uncertainty. The seed is fixed, so the output is
  deterministic.
- Leave-one-repo-out: for each repo, the calibration table is refitted on the others
  and checked on the held-out repo (monotonicity of observed rates per bin).
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
BANDS = [("low", 0, 24), ("medium", 25, 59), ("high", 60, 84), ("critical", 85, 100)]
WARN = ("KEEP", "VERIFY")
Z = 1.959964


def wilson(k: int, n: int) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": wilson(k, n)}


def load() -> tuple[dict[str, list[dict]], dict[str, dict]]:
    outcomes, summaries = {}, {}
    for f in sorted(RESULTS.glob("*.json")):
        if f.stem == "pooled":
            continue
        summaries[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        outcomes[f.stem] = [json.loads(line) for line in
                            (RESULTS / f"{f.stem}.jsonl").read_text(encoding="utf-8").splitlines()]
    return outcomes, summaries


def lift(rows: list[dict]) -> float | None:
    n = len(rows)
    k = sum(r["regretted"] for r in rows)
    w = [r for r in rows if r["verdict"] in WARN]
    if not n or not k or not w:
        return None
    return (sum(r["regretted"] for r in w) / len(w)) / (k / n)


def metrics(rows: list[dict], commits: int) -> dict:
    n = len(rows)
    k = sum(r["regretted"] for r in rows)
    w = [r for r in rows if r["verdict"] in WARN]
    tp = sum(r["regretted"] for r in w)
    warned_commits = len({(r.get("_repo"), r["sha"]) for r in w})
    out = {
        "removal_blocks": n, "regretted": k, "base_rate": rate(k, n),
        "warned_blocks": len(w), "precision": rate(tp, len(w)), "recall": rate(tp, k),
        "commits_evaluated": commits, "commits_warned": rate(warned_commits, commits),
        "lift": round(lift(rows), 2) if lift(rows) else None,
        "bands": {name: rate(sum(r["regretted"] for r in rows if lo <= r["risk"] <= hi),
                             sum(1 for r in rows if lo <= r["risk"] <= hi))
                  for name, lo, hi in BANDS},
    }
    return out


def bootstrap_lift(per_repo: dict[str, list[dict]], iters: int = 2000) -> list[float] | None:
    rng = random.Random(20261029)  # noqa: S311 - fixed-seed statistics, not security
    names = sorted(per_repo)
    vals = []
    for _ in range(iters):
        sample = [r for name in (rng.choice(names) for _ in names) for r in per_repo[name]]
        v = lift(sample)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    return [round(vals[int(0.025 * len(vals))], 2), round(vals[int(0.975 * len(vals)) - 1], 2)]


def loo(per_repo: dict[str, list[dict]]) -> list[dict]:
    from fence.scoring.calibration import fit

    out = []
    for held in sorted(per_repo):
        train = [(r["risk"], r["regretted"]) for name, rows in per_repo.items()
                 if name != held for r in rows]
        bins = fit(train)
        test = per_repo[held]
        observed = []
        for b in bins:
            xs = [r for r in test if b.risk_min <= r["risk"] <= b.risk_max]
            observed.append({"risk": f"{b.risk_min}-{b.risk_max}", "fit_rate": round(b.rate, 4),
                             **rate(sum(r["regretted"] for r in xs), len(xs))})
        rates = [o["rate"] for o in observed if o["n"]]
        pairs = list(zip(rates, rates[1:], strict=False))
        k = sum(r["regretted"] for r in test)
        lv = lift(test)
        out.append({
            "repo": held, "blocks": len(test), "regretted": k, "bins": observed,
            "monotonic_nondecreasing": (all(a <= b for a, b in pairs)
                                        if len(rates) > 1 else None),
            "monotonic_strict": (all(a < b for a, b in pairs)
                                 if len(rates) > 1 else None),
            "top_bin_above_bottom": (rates[-1] > rates[0] if len(rates) > 1 else None),
            "lift": round(lv, 2) if lv is not None else None,
        })
    return out


def to_md(p: dict) -> str:
    m = p["pooled"]

    def fmt(r: dict) -> str:
        if not r["n"]:
            return "n/a"
        lo, hi = r["ci95"]
        return f"{r['rate']:.1%} [{lo:.1%}, {hi:.1%}] ({r['k']}/{r['n']})"

    lines = [
        "# Pooled backtest", "",
        f"{len(p['repos'])} repos, {m['commits_evaluated']} commits, "
        f"{m['removal_blocks']} removal blocks, {m['regretted']} regretted.", "",
        "| Metric | Value (95% CI) |", "|---|---|",
        f"| Base rate (regretted / blocks) | {fmt(m['base_rate'])} |",
        f"| Precision (regretted / warned) | {fmt(m['precision'])} |",
        f"| Lift (precision / base) | {m['lift']}x, repo-bootstrap CI {p['lift_ci95']} |",
        f"| Recall | {fmt(m['recall'])} |",
        f"| Commits warned | {fmt(m['commits_warned'])} |", "",
        "## Regret rate by risk band", "", "| Band | Regret rate (95% CI) |", "|---|---|",
        *[f"| {b} | {fmt(r)} |" for b, r in m["bands"].items()], "",
        "## Per repo", "",
        "| Repo | Blocks | Regretted | Warned | Lift | Recall | Commits warned |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in p["per_repo"].items():
        rec = r["recall"]["rate"]
        lines.append(f"| {name} | {r['removal_blocks']} | {r['regretted']} | "
                     f"{r['warned_blocks']} | {r['lift'] if r['lift'] is not None else 'n/a'} | "
                     f"{'n/a' if rec is None else f'{rec:.0%}'} | "
                     f"{r['commits_warned']['rate']:.1%} |")
    lines += ["", "## Leave-one-repo-out calibration", "",
              "| Held out | Regretted | Observed rate per refit bin | Non-decreasing | "
              "Top > bottom |", "|---|---|---|---|---|"]
    for x in p["loo"]:
        bins = ", ".join(f"{b['risk']}: {b['rate']:.1%}" if b["n"] else f"{b['risk']}: -"
                         for b in x["bins"])
        lines.append(f"| {x['repo']} | {x['regretted']} | {bins} | "
                     f"{x['monotonic_nondecreasing']} | {x['top_bin_above_bottom']} |")
    s = p["loo_summary"]
    lines += ["", f"Out of sample: top bin above bottom bin on {s['top_above_bottom']}/"
              f"{s['repos_with_bins']} repos; non-decreasing on {s['nondecreasing']}/"
              f"{s['repos_with_bins']}; lift > 1 on {s['lift_above_1']}/{s['repos_with_lift']}."]
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.path.insert(0, str(ROOT))
    per_repo, summaries = load()
    for name, rows in per_repo.items():
        for r in rows:
            r["_repo"] = name
    pooled_rows = [r for rows in per_repo.values() for r in rows]
    commits = sum(s["commits_evaluated"] for s in summaries.values())
    lo = loo(per_repo)
    with_bins = [x for x in lo if x["top_bin_above_bottom"] is not None]
    with_lift = [x for x in lo if x["lift"] is not None]
    result = {
        "repos": sorted(per_repo),
        "pooled": metrics(pooled_rows, commits),
        "lift_ci95": bootstrap_lift(per_repo),
        "per_repo": {n: metrics(per_repo[n], summaries[n]["commits_evaluated"])
                     for n in sorted(per_repo)},
        "loo": lo,
        "loo_summary": {
            "repos_with_bins": len(with_bins),
            "top_above_bottom": sum(bool(x["top_bin_above_bottom"]) for x in with_bins),
            "nondecreasing": sum(bool(x["monotonic_nondecreasing"]) for x in with_bins),
            "repos_with_lift": len(with_lift),
            "lift_above_1": sum(x["lift"] > 1 for x in with_lift),
        },
    }
    (RESULTS / "pooled.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8",
                                         newline="\n")
    (RESULTS / "pooled.md").write_text(to_md(result), encoding="utf-8", newline="\n")
    print(to_md(result))


if __name__ == "__main__":
    main()
