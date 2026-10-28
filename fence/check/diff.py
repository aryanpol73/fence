"""Unified diff -> change blocks: runs of removed lines and the lines that replaced them.

A block is the unit Fence judges: "these base lines were removed, and these lines
took their place" (possibly none). Context lines end a block.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

from fence.git.runner import GitError, Repo

MAX_DIFF_BYTES = 20_000_000
MAX_FILES = 2_000


class DiffError(Exception):
    """The diff could not be produced or parsed."""


@dataclass
class Block:
    removed: list[tuple[int, str]]  # (base line number, text)
    added: list[tuple[int, str]]  # (new line number, text)

    @property
    def start(self) -> int:
        return self.removed[0][0]

    @property
    def end(self) -> int:
        return self.removed[-1][0]


@dataclass
class FileDiff:
    old_path: str | None  # None for added files
    new_path: str | None  # None for deleted files
    status: str  # added | deleted | modified | renamed
    blocks: list[Block] = field(default_factory=list)
    added_lines: list[str] = field(default_factory=list)  # every added line, for move detection
    binary: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def _strip(p: str | None, prefix: str) -> str | None:
    if p is None or p == "/dev/null":
        return None
    return p[len(prefix):] if p.startswith(prefix) else p


def parse_diff(text: str) -> list[FileDiff]:
    if len(text.encode("utf-8", "replace")) > MAX_DIFF_BYTES:
        raise DiffError(f"diff is larger than {MAX_DIFF_BYTES // 1_000_000} MB")
    try:
        patch = PatchSet(text)
    except UnidiffParseError as e:
        raise DiffError(f"could not parse diff: {e}") from None
    if len(patch) > MAX_FILES:
        raise DiffError(f"diff touches more than {MAX_FILES} files")
    out: list[FileDiff] = []
    for pf in patch:
        old = _strip(pf.source_file, "a/")
        new = _strip(pf.target_file, "b/")
        status = ("added" if pf.is_added_file else "deleted" if pf.is_removed_file
                  else "renamed" if pf.is_rename else "modified")
        fd = FileDiff(old, new, status, binary=pf.is_binary_file)
        for hunk in pf:
            removed: list[tuple[int, str]] = []
            added: list[tuple[int, str]] = []
            for line in hunk:
                value = line.value[:-1] if line.value.endswith("\n") else line.value
                if line.is_removed:
                    if added:  # a '-' after '+' starts a new block
                        _flush(fd, removed, added)
                        removed, added = [], []
                    removed.append((line.source_line_no, value))
                elif line.is_added:
                    added.append((line.target_line_no, value))
                    fd.added_lines.append(value)
                else:
                    _flush(fd, removed, added)
                    removed, added = [], []
            _flush(fd, removed, added)
        out.append(fd)
    return out


def _flush(fd: FileDiff, removed: list, added: list) -> None:
    if removed:
        fd.blocks.append(Block(list(removed), list(added)))


@dataclass(frozen=True)
class DiffSpec:
    """What to check. `base` is the revision removed lines are traced at."""

    base: str
    head: str | None = None  # None: working tree (or index with staged=True)
    staged: bool = False


def produce_diff(repo: Repo, spec: DiffSpec, *, rename_threshold: int = 50) -> tuple[str, str]:
    """Run git diff for `spec`. Returns (base_sha, diff_text)."""
    base = repo.verify_ref(spec.base)
    args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", f"-M{rename_threshold}%",
            "--src-prefix=a/", "--dst-prefix=b/", "--unified=3"]
    if spec.staged:
        args.append("--cached")
    args.append(base)
    if spec.head:
        args.append(repo.verify_ref(spec.head))
    args.append("--")
    try:
        return base, "".join(repo.stream(args, timeout=max(repo.timeout, 60)))
    except GitError as e:
        raise DiffError(str(e)) from None


def merge_base(repo: Repo, a: str, b: str) -> str:
    return repo.run(["merge-base", repo.verify_ref(a), repo.verify_ref(b)]).strip()
