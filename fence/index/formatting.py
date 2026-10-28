"""Detect formatting-only commits: every touched file has identical normalized tokens
before and after. Such commits are skipped by blame (like .git-blame-ignore-revs)
and never count as evidence.

Checking every commit would cost two blob reads per file across the whole history,
so only candidates are verified: commits whose message reads like a formatting
change, or whose every file kept its line count (re-indents, quote swaps). A
formatter commit that is neither is missed; listing it in .git-blame-ignore-revs
still works.
"""

from __future__ import annotations

from fence.git.history import LogEntry
from fence.git.runner import GitError, Repo
from fence.lang import tokens_for
from fence.signals.catalog import signal

MAX_FILES = 200
_STYLE = signal("text.formatting").pattern


def is_candidate(entry: LogEntry) -> bool:
    if len(entry.parents) != 1 or not entry.changes or len(entry.changes) > MAX_FILES:
        return False
    if any(c.status != "M" or c.added is None for c in entry.changes):
        return False  # adds, deletes, renames and binaries are not formatting
    worded = bool(_STYLE and _STYLE.search(f"{entry.subject}\n{entry.body}"))
    return worded or all(c.added == c.deleted for c in entry.changes)


def detect_format_only(repo: Repo, entries: list[LogEntry]) -> set[str]:
    """SHAs of formatting-only commits among `entries`. Two git processes per call."""
    cands = [e for e in entries if is_candidate(e)]
    if not cands:
        return set()
    specs = [(sha, c.path) for e in cands for c in e.changes for sha in (e.parents[0], e.sha)]
    try:
        blobs = repo.read_blobs(specs)
    except GitError:
        return set()
    out = set()
    for e in cands:
        parent = e.parents[0]
        for c in e.changes:
            old, new = blobs.get((parent, c.path)), blobs.get((e.sha, c.path))
            if old is None or new is None or old == new:
                break
            if tokens_for(c.path, old) != tokens_for(c.path, new):
                break
        else:
            out.add(e.sha)
    return out
