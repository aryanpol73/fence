"""`git blame` against an explicit base revision, parsed from --line-porcelain.

Two things here are security decisions, not style:
- `.git-blame-ignore-revs` is read from the BASE revision. In CI the working tree is
  the PR head, so reading it from disk would let a PR hide the commit that explains
  the code it deletes.
- `--ignore-revs-file=` (empty) is always passed first. It clears any list git would
  otherwise load from the repo's `blame.ignoreRevsFile` config, which points at the
  working tree.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from fence.git.runner import GitError, Repo, UnsafeInput

IGNORE_REVS_PATH = ".git-blame-ignore-revs"
MAX_IGNORE_REVS = 500
MAX_RANGES = 200
MAX_LINES = 5000

_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_HEADER = re.compile(r"^([0-9a-f]{40}|[0-9a-f]{64}) (\d+) (\d+)(?: \d+)?$")


@dataclass(frozen=True)
class BlameLine:
    sha: str
    orig_line: int  # line number in the commit that introduced it
    final_line: int  # line number at the base revision
    author: str
    author_mail: str
    author_time: int  # unix seconds
    author_tz: str
    summary: str
    filename: str  # path in `sha`; differs from the input path when -C found a copy
    previous_sha: str | None  # None on a root commit
    previous_path: str | None
    boundary: bool
    content: str
    # git 2.43 does not mark lines it could not move past an ignored commit, even
    # with blame.markUnblamableLines set, so we derive it: the line still points
    # at a commit we asked git to ignore.
    ignored: bool = False


def read_ignore_revs(repo: Repo, base_sha: str) -> list[str]:
    """Full SHAs listed in .git-blame-ignore-revs at `base_sha`. Missing file -> []."""
    listed = repo.run(["ls-tree", "--name-only", base_sha, "--", IGNORE_REVS_PATH]).strip()
    if not listed:
        return []
    text = repo.read_blob(base_sha, IGNORE_REVS_PATH, max_bytes=100_000)
    revs: list[str] = []
    for raw in text.split("\n"):
        line = raw.split("#", 1)[0].strip().lower()
        # Abbreviated SHAs and ref names are skipped: they could resolve differently
        # over time, and the file is repository text, so it is untrusted.
        if _SHA.fullmatch(line) and line not in revs:
            revs.append(line)
        if len(revs) >= MAX_IGNORE_REVS:
            break
    return revs


def blame(repo: Repo, base: str, path: str, ranges: Sequence[tuple[int, int]]) -> list[BlameLine]:
    """Blame the 1-based inclusive `ranges` of `path` at `base`. One git call per file."""
    base_sha = repo.verify_ref(base)
    rel = repo.safe_relpath(path)
    _check_ranges(ranges)
    ignore = read_ignore_revs(repo, base_sha)

    args = ["blame", "-w", "-C", "--line-porcelain", "--ignore-revs-file="]
    for sha in ignore:
        args += ["--ignore-rev", sha]
    for start, end in ranges:
        args += ["-L", f"{start},{end}"]
    args += [base_sha, "--", rel]

    lines = parse_line_porcelain(repo.run(args))
    expected = _distinct_line_count(ranges)
    if len(lines) != expected:
        raise GitError(f"blame returned {len(lines)} lines, expected {expected}")
    ignored = set(ignore)
    return [_with_ignored(bl, bl.sha in ignored) for bl in lines]


def parse_line_porcelain(output: str) -> list[BlameLine]:
    """Parse `git blame --line-porcelain`. Raises GitError on malformed or truncated output."""
    result: list[BlameLine] = []
    fields: dict[str, str] | None = None
    header: tuple[str, int, int] | None = None
    # Split on "\n" only: str.splitlines() would also split on \x0b, \x1c,   etc.
    # that can legitimately appear inside a line of code.
    rows = output.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    for row in rows:
        if header is None:
            m = _HEADER.match(row)
            if not m:
                raise GitError(f"unexpected blame header: {row[:80]!r}")
            header = (m.group(1), int(m.group(2)), int(m.group(3)))
            fields = {}
            continue
        if fields is None:  # unreachable: always set together with header
            raise GitError("blame parser lost its record state")
        if row.startswith("\t"):
            result.append(_build(header, fields, row[1:]))
            header, fields = None, None
            continue
        key, _, value = row.partition(" ")
        if key == "boundary":
            fields["boundary"] = "1"
        elif key in _KEPT_KEYS:
            fields[key] = value
        # committer-*, unknown keys: skipped on purpose.
    if header is not None:
        raise GitError("truncated blame output: header without content line")
    return result


_KEPT_KEYS = {
    "author",
    "author-mail",
    "author-time",
    "author-tz",
    "summary",
    "filename",
    "previous",
}


def _build(header: tuple[str, int, int], f: dict[str, str], content: str) -> BlameLine:
    sha, orig, final = header
    for required in ("author", "author-time", "filename"):
        if required not in f:
            raise GitError(f"blame record for {sha[:12]} is missing {required!r}")
    prev_sha = prev_path = None
    if "previous" in f:
        prev_sha, _, prev_path = f["previous"].partition(" ")
        if not _SHA.fullmatch(prev_sha) or not prev_path:
            raise GitError(f"malformed 'previous' in blame record for {sha[:12]}")
    try:
        author_time = int(f["author-time"])
    except ValueError:
        raise GitError(f"malformed author-time for {sha[:12]}") from None
    return BlameLine(
        sha=sha,
        orig_line=orig,
        final_line=final,
        author=f["author"],
        author_mail=f.get("author-mail", "").strip("<>"),
        author_time=author_time,
        author_tz=f.get("author-tz", ""),
        summary=f.get("summary", ""),
        filename=f["filename"],
        previous_sha=prev_sha,
        previous_path=prev_path,
        boundary="boundary" in f,
        content=content,
    )


def _with_ignored(bl: BlameLine, ignored: bool) -> BlameLine:
    if not ignored:
        return bl
    return BlameLine(**{**bl.__dict__, "ignored": True})


def _check_ranges(ranges: Sequence[tuple[int, int]]) -> None:
    if not ranges:
        raise UnsafeInput("no line ranges given")
    if len(ranges) > MAX_RANGES:
        raise UnsafeInput(f"too many ranges ({len(ranges)} > {MAX_RANGES})")
    for start, end in ranges:
        if not (isinstance(start, int) and isinstance(end, int)) or start < 1 or end < start:
            raise UnsafeInput(f"bad line range: {start},{end}")
    if _distinct_line_count(ranges) > MAX_LINES:
        raise UnsafeInput(f"too many lines to blame (> {MAX_LINES})")


def _distinct_line_count(ranges: Sequence[tuple[int, int]]) -> int:
    # git merges overlapping -L ranges, so count distinct lines, not the sum.
    seen: set[int] = set()
    for start, end in ranges:
        seen.update(range(start, end + 1))
    return len(seen)
