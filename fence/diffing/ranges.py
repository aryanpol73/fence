"""The unit of work between diff parsing and git tracing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RemovedRange:
    """Consecutive pre-image lines a diff removes or changes.

    `start`..`end` are 1-based, inclusive line numbers in the file at the base revision.
    `lines` is the removed text, without newlines, exactly as the diff shows it.
    """

    path: str
    start: int
    end: int
    lines: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.start < 1 or self.end < self.start:
            raise ValueError(f"bad range {self.start},{self.end}")
        if len(self.lines) != self.end - self.start + 1:
            raise ValueError("line count does not match range")
