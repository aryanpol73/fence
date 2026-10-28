"""The signal catalog: every pattern Fence reads as evidence, each with an id and weight.

Weights are Fence Strength points. They are starting values, tuned by the backtest
(see DECISIONS.md), and overridable in fence.toml under [scoring.weights].
Every signal here has its own positive and negative unit test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["text", "label", "review", "code", "structural", "negative"]

# Order matters only for picking a decision type: the first matching type wins.
DECISION_TYPES = (
    "incident",
    "security",
    "data-integrity",
    "concurrency",
    "revert",
    "bugfix",
    "compatibility",
    "dependency-pin",
    "performance",
    "deliberate-design",
    "cleanup",
    "unknown",
)


@dataclass(frozen=True)
class Signal:
    id: str
    kind: Kind
    weight: float
    description: str
    decision_type: str | None = None
    pattern: re.Pattern[str] | None = None


def _rx(p: str) -> re.Pattern[str]:
    return re.compile(p, re.IGNORECASE)


TEXT_SIGNALS: tuple[Signal, ...] = (
    Signal("text.incident", "text", 20, "mentions an outage, incident or postmortem", "incident",
           _rx(r"\b(outage|incident|post-?mortem|sev[ -]?[0-4]|downtime|page[ds]? on-?call)\b")),
    Signal("text.security", "text", 22, "mentions a vulnerability or security fix", "security",
           _rx(r"\b(cve-\d{4}-\d{4,}|security|xss|csrf|ssrf|sql injection|injection|"
               r"vulnerab\w*|exploit\w*|buffer overflow|auth(entication)? bypass|sanitiz\w*)\b")),
    Signal("text.data_integrity", "text", 18, "mentions data loss, duplicates or idempotency",
           "data-integrity",
           _rx(r"\b(duplicate[sd]?|corrupt\w*|data loss|idempoten\w*|integrity|"
               r"double[- ]charg\w*|lost (writes|updates|data))\b")),
    Signal("text.concurrency", "text", 16, "mentions a race, deadlock or thread safety",
           "concurrency",
           _rx(r"\b(race condition|data race|races?|deadlock\w*|thread[- ]safe\w*|"
               r"concurren\w*|mutex|atomic\w*)\b")),
    Signal("text.hotfix", "text", 14, "marked as a hotfix", "incident", _rx(r"\bhot-?fix\w*\b")),
    Signal("text.regression", "text", 12, "mentions a regression", "bugfix",
           _rx(r"\bregress(ion|ions|ed)?\b")),
    Signal("text.do_not_remove", "text", 18, "says the code must stay", "deliberate-design",
           _rx(r"\b(do not remove|don'?t remove|do not delete|don'?t delete|must not be removed|"
               r"needed because|required because|is necessary|load[- ]bearing)\b")),
    Signal("text.fix", "text", 8, "describes a fix", "bugfix",
           _rx(r"\b(fix|fixes|fixed|fixing|bug|bugs|bugfix|patch(ed)?|crash\w*|broken)\b")),
    Signal("text.leak", "text", 10, "mentions a leak or out-of-memory", "bugfix",
           _rx(r"\b(memory leak|leak\w*|oom|out of memory)\b")),
    Signal("text.workaround", "text", 10, "describes a workaround", "compatibility",
           _rx(r"\b(workaround|work around|temporary|temp fix|hack|kludge)\b")),
    Signal("text.compat", "text", 8, "mentions compatibility or a backport", "compatibility",
           _rx(r"\b(compat\w*|backport\w*|polyfill|shim)\b")),
    Signal("text.dependency_pin", "text", 10, "pins or holds back a dependency", "dependency-pin",
           _rx(r"\b(pin(ned|s)? (to|at)|hold back|downgrade\w*|lock (to|at) version)\b")),
    Signal("text.performance", "text", 6, "mentions performance", "performance",
           _rx(r"\b(performance|perf|latency|slow(er|ness)?|speed ?up|optimi[sz]\w*)\b")),
    Signal("text.rollback", "text", 6, "mentions a rollback", "revert",
           _rx(r"\b(roll ?back|rolled back)\b")),
    Signal("text.closes_issue", "text", 12, "closes or fixes a linked issue", None,
           _rx(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s*:?\s+(?P<v>(?:[\w.-]+/[\w.-]+)?#\d+)")),
    Signal("text.issue_ref", "text", 4, "references an issue or PR", None,
           _rx(r"(?<![\w/&])(?P<v>(?:[\w.-]+/[\w.-]+)?#\d+)\b")),
    Signal("text.ticket", "text", 8, "references a ticket", None,
           re.compile(r"\b(?P<v>[A-Z][A-Z0-9]{1,9}-\d{1,7})\b")),
    Signal("text.incident_url", "text", 12, "links an incident or status page", "incident",
           _rx(r"(?P<v>https?://[^\s)>\]]*(?:status|incident|pagerduty|opsgenie|postmortem)"
               r"[^\s)>\]]*)")),
    # Negative signals: these touches say little about why code exists.
    Signal("text.formatting", "negative", -15, "a formatting or lint change", "cleanup",
           _rx(r"\b(reformat\w*|format(ted|ting)? (code|files|with)|formatter|lint(ing)?|"
               r"prettier|black|gofmt|isort|whitespace|code style)\b")),
    Signal("text.refactor", "negative", -6, "a refactor or cleanup", "cleanup",
           _rx(r"\b(refactor\w*|clean ?up|cleaned up|tidy|tidied|rename[sd]?)\b")),
    Signal("text.automated", "negative", -10, "an automated or generated change", "cleanup",
           _rx(r"\b(auto-?generated|regenerat\w*|dependabot|renovate(bot)?|"
               r"bump(ed|s)? [\w@/.-]+ from)\b")),
)

# PR/issue labels, matched against whole label names (case-insensitive).
LABEL_SIGNALS: tuple[Signal, ...] = (
    Signal("label.security", "label", 22, "labelled security", "security",
           _rx(r"^(security|vulnerability|cve)\b")),
    Signal("label.incident", "label", 20, "labelled incident/hotfix/p0", "incident",
           _rx(r"^(incident|hotfix|p0|sev[ -]?[0-2]|outage|critical)\b")),
    Signal("label.regression", "label", 12, "labelled regression", "bugfix",
           _rx(r"^regression\b")),
    Signal("label.bug", "label", 8, "labelled bug", "bugfix",
           _rx(r"^(type: ?)?(bug|defect|fix)\b")),
    Signal("label.performance", "label", 6, "labelled performance", "performance",
           _rx(r"^(perf|performance)\b")),
    Signal("label.chore", "negative", -6, "labelled chore/refactor/dependencies", "cleanup",
           _rx(r"^(chore|refactor|dependencies|deps|documentation|docs)\b")),
)


def extract_labels(labels: list[str]) -> list[str]:
    """Signal ids for a list of label names, in catalog order."""
    out = []
    for s in LABEL_SIGNALS:
        if any(s.pattern.search(str(lb).strip()) for lb in labels if s.pattern):
            out.append(s.id)
    return out


_BY_ID = {s.id: s for s in TEXT_SIGNALS + LABEL_SIGNALS}


def signal(signal_id: str) -> Signal:
    return _BY_ID[signal_id]


def all_signals() -> tuple[Signal, ...]:
    from fence.signals.code import CODE_SIGNALS, STRUCTURAL_SIGNALS

    return TEXT_SIGNALS + CODE_SIGNALS + STRUCTURAL_SIGNALS


MAX_DETAIL = 120


def extract_text(text: str, *, skip_refs: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """(signal_id, verbatim matched text) for every text signal in `text`. Deterministic order."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    closing: set[str] = set()
    for s in TEXT_SIGNALS:
        assert s.pattern is not None  # noqa: S101 - every text signal has one
        for m in s.pattern.finditer(text):
            # Signals with a value to cite name it (?P<v>...); others cite the whole match.
            detail = (m.group("v") if "v" in s.pattern.groupindex else m.group(0))[:MAX_DETAIL]
            if s.id == "text.closes_issue":
                closing.add(detail.lower())
            if s.id == "text.issue_ref" and (detail.lower() in closing or detail in skip_refs):
                continue  # already counted as a closing reference, or it is the PR itself
            key = (s.id, detail)
            if key not in seen:
                seen.add(key)
                found.append(key)
    return found
