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


def detect_format_only(repo: Repo, entry: LogEntry) -> bool:
    if not is_candidate(entry):
        return False
    parent = entry.parents[0]
    specs = [(sha, c.path) for c in entry.changes for sha in (parent, entry.sha)]
    try:
        blobs = repo.read_blobs(specs)
    except GitError:
        return False
    for c in entry.changes:
        old, new = blobs.get((parent, c.path)), blobs.get((entry.sha, c.path))
        if old is None or new is None or old == new:
            return False
        if tokens_for(c.path, old) != tokens_for(c.path, new):
            return False
    return True
