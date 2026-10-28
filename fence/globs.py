"""Gitignore-style globs for include/exclude/ignore_paths: `*`, `?`, `**`."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    p = pattern.strip().lstrip("/")
    if p.endswith("/"):
        p += "**"
    out = []
    i = 0
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    # A pattern without a slash matches at any depth, like .gitignore.
    prefix = "" if "/" in pattern.strip().rstrip("/") else "(?:.*/)?"
    return re.compile(prefix + "".join(out) + r"\Z")


def match_any(path: str, patterns: Iterable[str]) -> bool:
    return any(_compile(p).match(path) for p in patterns)


def included(path: str, include: Iterable[str], exclude: Iterable[str]) -> bool:
    return match_any(path, include) and not match_any(path, exclude)
