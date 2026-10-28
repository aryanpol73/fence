"""Decision Records and their evidence: the objects every interface returns.

JSON output is versioned (`SCHEMA`). A field is never renamed or retyped within a
major version; new optional fields may be added.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

SCHEMA = "fence/v1"

EvidenceType = Literal[
    "commit", "pr", "issue", "review_comment", "revert", "code_comment", "test", "ticket",
    "incident_link", "obsolescence",
]
RetrievalStatus = Literal["ok", "not_fetched", "unreachable", "orphaned", "not_indexed"]
Status = Literal["active", "superseded", "reverted", "orphaned", "stale-suspected"]
Grade = Literal["A", "B", "C", "D"]


@dataclass(frozen=True)
class Evidence:
    id: str  # E1, E2 ... unique within one response
    type: EvidenceType
    ref: str  # SHA, "#123", "path:line@rev", or a URL
    url: str | None
    timestamp: int | None  # unix seconds
    author: str | None
    quote: str | None  # verbatim, cleaned for display, length-capped
    retrieval_status: RetrievalStatus = "ok"


@dataclass(frozen=True)
class Anchor:
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    language: str | None
    path_history: tuple[str, ...] = ()
    fingerprint: str | None = None


@dataclass(frozen=True)
class Factor:
    id: str
    points: float
    reason: str  # plain English


@dataclass(frozen=True)
class Score:
    value: int
    band: str
    confidence: float
    factors: tuple[Factor, ...]
    what_would_change: tuple[str, ...] = ()

    def top(self, n: int = 3) -> tuple[Factor, ...]:
        return tuple(sorted(self.factors, key=lambda f: (-abs(f.points), f.id))[:n])


@dataclass
class DecisionRecord:
    id: str
    anchors: list[Anchor]
    type: str
    grade: Grade
    stated_reason: str | None  # verbatim quote or None, never paraphrase
    stated_reason_evidence: str | None  # evidence id the quote comes from
    inferred_reason: str | None  # always prefixed "Inferred:"
    constraints: list[str]
    alternatives_considered: list[str]
    evidence: list[Evidence]
    introduced_sha: str
    introduced_at: int
    introduced_path: str
    last_touched_at: int
    authors: list[str]
    status: Status
    strength: Score
    signals: list[tuple[str, str]] = field(default_factory=list)
    later_touches: list[str] = field(default_factory=list)  # SHAs, newest first
    supersedes: list[str] = field(default_factory=list)
    superseded_by: list[str] = field(default_factory=list)
    pr_number: int | None = None

    @property
    def reason_text(self) -> str:
        if self.stated_reason:
            return self.stated_reason
        if self.inferred_reason:
            return self.inferred_reason
        return "Reason unknown: no stated or inferable reason found in history."

    def to_dict(self) -> dict:
        d = asdict(self)
        d["strength"]["top_factors"] = [asdict(f) for f in self.strength.top()]
        d["signals"] = [{"id": s, "detail": det} for s, det in self.signals]
        d["reason"] = self.reason_text
        return d
