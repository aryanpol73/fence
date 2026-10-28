"""Stream `git log --raw --numstat -z` and parse it into commits with file changes.

Commit messages are untrusted and may contain any byte except NUL, including our
field separators. So every marker carries a random per-run nonce: a message
cannot forge a record boundary it cannot predict.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from fence.git.runner import GitError, Repo

_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


@dataclass
class RawChange:
    path: str
    old_path: str | None
    status: str  # A C D M R T U X (git --raw)
    similarity: int | None
    added: int | None = None  # None for binary files
    deleted: int | None = None


@dataclass
class LogEntry:
    sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    author_time: int
    committer_time: int
    subject: str
    body: str
    changes: list[RawChange] = field(default_factory=list)


class LogFormat:
    def __init__(self) -> None:
        nonce = secrets.token_hex(8)
        self.start = f"\x1e{nonce}S"
        self.sep = f"\x1f{nonce}F"
        self.end = f"\x1d{nonce}E"
        fields = ["%H", "%P", "%an", "%ae", "%at", "%ct", "%s", "%b"]
        f_sep = f"%x1f{nonce}F"
        self.arg = f"--format=%x1e{nonce}S" + f_sep.join(fields) + f"%x1d{nonce}E"


def log_args(fmt: LogFormat, *, rename_threshold: int = 50) -> list[str]:
    return [
        "log",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--diff-merges=off",  # merges carry no diff; their PR mapping comes from the subject
        "--raw",
        "--numstat",
        "-z",
        "--no-abbrev",
        f"-M{rename_threshold}%",
        fmt.arg,
    ]


def iter_log(
    repo: Repo, revs: Iterable[str], *, fmt: LogFormat | None = None, extra: Iterable[str] = (),
    rename_threshold: int = 50, timeout: float = 600.0,
) -> Iterator[LogEntry]:
    """Yield commits reachable from `revs` (already-validated SHAs or `--not`), oldest first."""
    fmt = fmt or LogFormat()
    args = [*log_args(fmt, rename_threshold=rename_threshold), "--reverse", *extra, *revs, "--"]
    yield from parse_stream(repo.stream(args, timeout=timeout), fmt)


def parse_stream(chunks: Iterable[str], fmt: LogFormat) -> Iterator[LogEntry]:
    buf = ""
    for chunk in chunks:
        buf += chunk
        # Everything before the last start marker is complete.
        last = buf.rfind(fmt.start)
        if last <= 0:
            continue
        complete, buf = buf[:last], buf[last:]
        yield from _parse_records(complete, fmt)
    if buf:
        yield from _parse_records(buf, fmt)


def _parse_records(text: str, fmt: LogFormat) -> Iterator[LogEntry]:
    for record in text.split(fmt.start):
        if record.strip("\x00\n") == "":
            continue
        yield parse_record(record, fmt)


def parse_record(record: str, fmt: LogFormat) -> LogEntry:
    header, end, rest = record.partition(fmt.end)
    if not end:
        raise GitError("truncated git log record")
    parts = header.split(fmt.sep)
    if len(parts) != 8 or not _SHA.fullmatch(parts[0]):
        raise GitError(f"unexpected git log header: {header[:80]!r}")
    sha, parents, an, ae, at, ct, subject, body = parts
    if not (at.isdigit() and ct.isdigit()):
        raise GitError(f"bad timestamps in {sha[:12]}")
    entry = LogEntry(
        sha=sha,
        parents=tuple(p for p in parents.split(" ") if p),
        author_name=an,
        author_email=ae,
        author_time=int(at),
        committer_time=int(ct),
        subject=subject,
        body=body.strip("\n"),
    )
    entry.changes = _parse_changes(rest)
    return entry


def _parse_changes(rest: str) -> list[RawChange]:
    tokens = rest.split("\x00")
    raw: dict[str, RawChange] = {}
    order: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        head = tok.lstrip("\n")
        if head == "":
            i += 1
            continue
        if head.startswith(":"):
            fields = head[1:].split(" ")
            if len(fields) < 5 or i + 1 >= n:
                raise GitError(f"malformed raw diff entry: {head[:60]!r}")
            status = fields[4]
            letter = status[:1]
            score = int(status[1:]) if status[1:].isdigit() else None
            if letter in ("R", "C"):
                old, new = tokens[i + 1], tokens[i + 2]
                i += 3
            else:
                old, new = None, tokens[i + 1]
                i += 2
            raw[new] = RawChange(new, old, letter, score)
            order.append(new)
            continue
        cols = head.split("\t")
        if len(cols) != 3:
            raise GitError(f"malformed numstat entry: {head[:60]!r}")
        added, deleted, path = cols
        if path == "":  # rename: old and new follow as separate tokens
            if i + 2 >= n:
                raise GitError("truncated numstat rename entry")
            path = tokens[i + 2]
            i += 3
        else:
            i += 1
        change = raw.get(path)
        if change is None:
            change = RawChange(path, None, "M", None)
            raw[path] = change
            order.append(path)
        change.added = int(added) if added.isdigit() else None
        change.deleted = int(deleted) if deleted.isdigit() else None
    return [raw[p] for p in order]
