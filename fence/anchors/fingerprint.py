"""Content fingerprints: find the same code again after it moves or is reformatted.

`exact` is a hash of the normalized token stream: equal means "same code,
modulo layout, comments and quote style". `shingles` are hashed k-token windows:
their Jaccard similarity says how much code two snippets share, which survives
renames of the surrounding file, moves between files and small edits.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

SHINGLE_K = 5
SAME_CODE_JACCARD = 0.7


@dataclass(frozen=True)
class Fingerprint:
    exact: str
    shingles: frozenset[int]
    token_count: int

    def to_json(self) -> dict:
        return {"exact": self.exact, "shingles": sorted(self.shingles), "n": self.token_count}

    @classmethod
    def from_json(cls, d: dict) -> Fingerprint:
        return cls(d["exact"], frozenset(d["shingles"]), d["n"])


def fingerprint(tokens: Sequence[str], k: int = SHINGLE_K) -> Fingerprint:
    joined = "\x00".join(tokens).encode("utf-8", errors="replace")
    exact = hashlib.sha256(joined).hexdigest()[:24]
    return Fingerprint(exact, shingles(tokens, k), len(tokens))


def shingles(tokens: Sequence[str], k: int = SHINGLE_K) -> frozenset[int]:
    if not tokens:
        return frozenset()
    if len(tokens) < k:
        windows = [tuple(tokens)]
    else:
        windows = [tuple(tokens[i : i + k]) for i in range(len(tokens) - k + 1)]
    out = set()
    for w in windows:
        h = hashlib.blake2b("\x00".join(w).encode("utf-8", "replace"), digest_size=8)
        out.add(int.from_bytes(h.digest(), "big") >> 1)  # fits in a signed 64-bit int
    return frozenset(out)


def jaccard(a: frozenset[int], b: frozenset[int]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(needle: frozenset[int], haystack: frozenset[int]) -> float:
    """Share of `needle`'s shingles found in `haystack` (moved code inside a bigger file)."""
    if not needle:
        return 0.0
    return len(needle & haystack) / len(needle)


def same_code(a: Fingerprint, b: Fingerprint, threshold: float = SAME_CODE_JACCARD) -> bool:
    return a.exact == b.exact or jaccard(a.shingles, b.shingles) >= threshold
