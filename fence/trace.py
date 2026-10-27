"""Removed ranges -> the commits that introduced them, at an explicit base revision.

Plain pipeline code: no model calls, no guessing. If the diff does not match the
base, we stop with PreimageMismatch instead of blaming lines that might be different.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from fence.diffing.ranges import RemovedRange
from fence.diffing.symbols import Symbol, enclosing_symbol
from fence.git.blame import BlameLine, blame
from fence.git.runner import GitError, Repo


class PreimageMismatch(GitError):
    """The diff's removed lines are not what the base revision contains."""


@dataclass(frozen=True)
class TraceResult:
    range: RemovedRange
    symbol: Symbol | None
    lines: tuple[BlameLine, ...]

    @property
    def introducing(self) -> tuple[BlameLine, ...]:
        """One BlameLine per distinct commit, in line order."""
        seen: dict[str, BlameLine] = {}
        for bl in self.lines:
            seen.setdefault(bl.sha, bl)
        return tuple(seen.values())


def trace(repo: Repo, base: str, ranges: Sequence[RemovedRange]) -> list[TraceResult]:
    base_sha = repo.verify_ref(base)
    by_path: dict[str, list[RemovedRange]] = {}
    for r in ranges:
        by_path.setdefault(r.path, []).append(r)

    results: list[TraceResult] = []
    for path, path_ranges in by_path.items():
        text = repo.read_blob(base_sha, path)
        base_lines = text.split("\n")
        for r in path_ranges:
            actual = base_lines[r.start - 1 : r.end]
            if list(r.lines) != actual:
                raise PreimageMismatch(
                    f"{path}:{r.start}-{r.end} in the diff does not match {base_sha[:12]}; "
                    "was the diff made against a different base?"
                )
        spans = [(r.start, r.end) for r in path_ranges]
        blamed = {bl.final_line: bl for bl in blame(repo, base_sha, path, spans)}
        for r in path_ranges:
            lines = tuple(blamed[n] for n in range(r.start, r.end + 1))
            results.append(TraceResult(r, enclosing_symbol(text, r.start), lines))
    return results
