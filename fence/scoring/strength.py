"""Fence Strength (how load-bearing code looks) and Change Risk (how risky an edit is).

Both are sums of named factors. Each factor carries a plain-English reason, so
`fence explain` can show exactly how a number was reached. There is no hidden
term: value = clamp(sum(points), 0, 100).

Default weights are starting values; DECISIONS.md records changes made after
running the backtest. Any weight can be overridden in fence.toml under
[scoring.weights] using the keys in WEIGHTS.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fence.config import Config
from fence.decisions.model import Factor, Score

WEIGHTS: dict[str, float] = {
    # origin type (strongest type only)
    "type.incident": 25, "type.security": 25, "type.data-integrity": 22,
    "type.concurrency": 18, "type.revert": 14, "type.bugfix": 12, "type.compatibility": 10,
    "type.dependency-pin": 10, "type.performance": 8, "type.deliberate-design": 10,
    "type.cleanup": 0, "type.unknown": 0,
    # evidence quality
    "grade.A": 15, "grade.B": 10, "grade.C": 4, "grade.D": 0,
    # links
    "link.each": 5, "link.cap": 15,
    "corroboration.each": 3, "corroboration.cap": 9,
    # code
    "code.cap": 12,
    # history shape
    "survival.per_year": 3, "survival.cap": 9, "survival.min_later_commits": 3,
    "regression.each": 8, "regression.cap": 24,
    "revert.involved": 14,
    "test.added": 10,
    "authors.each": 3, "authors.cap": 9,
    # negatives
    "neg.bulk": -10, "neg.vendored": -30, "neg.automated": -10, "neg.refactor": -6,
    # change risk
    "kind.delete": 1.0, "kind.loosen": 0.85, "kind.alter": 0.5, "kind.move": 0.3,
    "risk.tests_removed": 10, "risk.linked_tests_touched": 5,
}

BASE_CONFIDENCE = {"A": 0.85, "B": 0.75, "C": 0.5, "D": 0.3}


def weights(cfg: Config) -> dict[str, float]:
    unknown = set(cfg.scoring.weights) - set(WEIGHTS)
    if unknown:
        raise ValueError(f"unknown scoring weight(s): {', '.join(sorted(unknown))}")
    return {**WEIGHTS, **cfg.scoring.weights}


@dataclass
class StrengthInputs:
    decision_type: str
    grade: str
    links: list[str] = field(default_factory=list)  # distinct issue/PR/ticket/incident refs
    positive_text_signals: list[str] = field(default_factory=list)  # distinct signal ids
    code_signals: list[tuple[str, float]] = field(default_factory=list)  # (id, weight)
    age_days: float = 0.0
    later_commits_to_file: int = 0
    regression_fixes: int = 0
    revert_involved: bool = False
    test_added: bool = False
    distinct_authors: int = 1
    bulk: bool = False
    vendored: bool = False
    automated: bool = False
    refactor: bool = False
    # coverage, for confidence
    shallow: bool = False
    not_indexed: bool = False
    pr_unfetched: bool = False
    offline: bool = False


def strength(inp: StrengthInputs, cfg: Config) -> Score:
    w = weights(cfg)
    f: list[Factor] = []

    def add(fid: str, points: float, reason: str) -> None:
        if points:
            f.append(Factor(fid, round(points, 1), reason))

    t = inp.decision_type
    add(f"type.{t}", w.get(f"type.{t}", 0), f"introduced as a {t} change")
    add(f"grade.{inp.grade}", w[f"grade.{inp.grade}"], _grade_reason(inp.grade))
    if inp.links:
        n = len(inp.links)
        add("link", min(w["link.cap"], n * w["link.each"]),
            f"linked to {n} issue/PR/ticket reference(s): {', '.join(inp.links[:3])}")
    extra = max(0, len(inp.positive_text_signals) - 1)
    if extra:
        add("corroboration", min(w["corroboration.cap"], extra * w["corroboration.each"]),
            f"{extra + 1} independent text signals agree")
    if inp.code_signals:
        pts = min(w["code.cap"], sum(x for _, x in inp.code_signals))
        kinds = {sid.split(".", 1)[1].replace("_", " ") for sid, _ in inp.code_signals}
        names = ", ".join(sorted(kinds))
        add("code", pts, f"the code itself contains: {names}")
    if inp.later_commits_to_file >= w["survival.min_later_commits"] and inp.age_days >= 180:
        years = inp.age_days / 365
        add("survival", min(w["survival.cap"], years * w["survival.per_year"]),
            f"survived {inp.later_commits_to_file} later commits to the file "
            f"over {years:.1f} years")
    if inp.regression_fixes:
        add("regression", min(w["regression.cap"], inp.regression_fixes * w["regression.each"]),
            f"these lines were fixed again {inp.regression_fixes} time(s) later")
    if inp.revert_involved:
        add("revert", w["revert.involved"], "involved in a revert (reverted, or re-applied)")
    if inp.test_added:
        add("test", w["test.added"], "a test was added in the same change")
    if inp.distinct_authors > 1:
        add("authors", min(w["authors.cap"], (inp.distinct_authors - 1) * w["authors.each"]),
            f"{inp.distinct_authors} people have touched these lines")
    if inp.bulk:
        add("neg.bulk", w["neg.bulk"], "landed in a very large bulk commit")
    if inp.vendored:
        add("neg.vendored", w["neg.vendored"], "vendored or generated path")
    if inp.automated:
        add("neg.automated", w["neg.automated"], "an automated or dependency-bot change")
    if inp.refactor:
        add("neg.refactor", w["neg.refactor"], "described as a refactor or cleanup")

    value = int(round(max(0.0, min(100.0, sum(x.points for x in f)))))
    return Score(value, cfg.scoring.band(value), confidence(inp), tuple(f), _what_would_change(inp))


def confidence(inp: StrengthInputs) -> float:
    c = BASE_CONFIDENCE[inp.grade]
    if inp.shallow:
        c -= 0.15
    if inp.not_indexed:
        c -= 0.2
    if inp.pr_unfetched:
        c -= 0.05
    if inp.offline and inp.grade in ("C", "D"):
        c -= 0.05
    return round(max(0.05, min(0.95, c)), 2)


def change_risk(
    s: Score, kind: str, cfg: Config, *, tests_removed: bool = False,
    path_bonus: int = 0, path_bonus_reason: str = "",
) -> Score:
    w = weights(cfg)
    mult = w[f"kind.{kind}"]
    f = [Factor("strength", round(s.value * mult, 1),
                f"Fence Strength {s.value} x {mult} for a '{kind}' change")]
    if tests_removed:
        f.append(Factor("risk.tests_removed", w["risk.tests_removed"],
                        "tests are removed in the same change"))
    if path_bonus:
        f.append(Factor("risk.path", path_bonus, path_bonus_reason or "sensitive path"))
    value = int(round(max(0.0, min(100.0, sum(x.points for x in f)))))
    return Score(value, cfg.scoring.band(value), s.confidence, (*f, *s.factors),
                 s.what_would_change)


def _grade_reason(g: str) -> str:
    return {
        "A": "reason stated in a PR, issue or review linked to the change",
        "B": "reason stated in the commit message or a code comment",
        "C": "reason inferred from change shape and labels only",
        "D": "no reason recoverable",
    }[g]


def _what_would_change(inp: StrengthInputs) -> tuple[str, ...]:
    out = []
    if inp.grade in ("C", "D"):
        out.append("state the reason in a code comment or the PR description (raises grade)")
    if not inp.links:
        out.append("link the issue or incident it fixes")
    if not inp.test_added:
        out.append("add a test that fails without this code")
    if inp.not_indexed:
        out.append("run `fence index` so history is complete")
    if inp.pr_unfetched:
        out.append("set GITHUB_TOKEN and re-run `fence index` to read PR and issue text")
    return tuple(out[:3])
