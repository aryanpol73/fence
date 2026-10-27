"""`git log -L` for walking further back than blame when a commit explains nothing."""

from __future__ import annotations

from dataclasses import dataclass

from fence.git.runner import GitError, Repo, UnsafeInput

DEFAULT_MAX_COUNT = 20
MAX_COUNT_LIMIT = 200

_FIELD = "\x1f"
_RECORD = "\x1e"


@dataclass(frozen=True)
class LogCommit:
    sha: str
    author: str
    author_mail: str
    date: str  # strict ISO 8601 author date
    subject: str


def log_line_range(
    repo: Repo, rev: str, path: str, start: int, end: int, *, max_count: int = DEFAULT_MAX_COUNT
) -> list[LogCommit]:
    """Commits that touched lines `start`..`end` of `path` as of `rev`, newest first."""
    sha = repo.verify_ref(rev)
    rel = repo.safe_relpath(path)
    if start < 1 or end < start:
        raise UnsafeInput(f"bad line range: {start},{end}")
    if not 1 <= max_count <= MAX_COUNT_LIMIT:
        raise UnsafeInput(f"max_count must be 1..{MAX_COUNT_LIMIT}")
    out = repo.run(
        [
            "log",
            f"-L{start},{end}:{rel}",
            "-s",  # metadata only; the patch text is never needed here
            "--no-color",
            "--no-ext-diff",
            f"--max-count={max_count}",
            f"--format=%H{_FIELD}%an{_FIELD}%ae{_FIELD}%aI{_FIELD}%s{_RECORD}",
            sha,
        ]
    )
    return parse_log(out)


def parse_log(output: str) -> list[LogCommit]:
    commits: list[LogCommit] = []
    for record in output.split(_RECORD):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(_FIELD)
        if len(parts) != 5 or len(parts[0]) not in (40, 64):
            raise GitError(f"unexpected git log record: {record[:80]!r}")
        commits.append(LogCommit(*parts))
    return commits
