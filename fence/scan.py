"""`fence scan DIR`: every decision in a directory, ranked by "do not simply delete"."""

from __future__ import annotations

from dataclasses import dataclass, field

from fence.context import Context
from fence.decisions.build import WhyError, why
from fence.decisions.model import DecisionRecord
from fence.git.runner import GitError
from fence.globs import match_any
from fence.lang import plugin_for

MAX_FILES = 300


@dataclass
class ScanResult:
    path: str
    decisions: list[tuple[str, DecisionRecord]] = field(default_factory=list)  # (file, record)
    files_scanned: int = 0
    files_skipped: int = 0
    notes: list[str] = field(default_factory=list)


def scan(ctx: Context, path: str = ".", *, rev: str = "HEAD", max_files: int = MAX_FILES,
         deep: bool = False) -> ScanResult:
    rev_sha = ctx.repo.verify_ref(rev)
    rel = "." if path in (".", "") else ctx.repo.safe_relpath(path)
    out = ctx.repo.run(["ls-tree", "-r", "--name-only", "-z", rev_sha, "--", rel])
    files = [f for f in out.split("\x00") if f]
    files = [f for f in files if not match_any(f, ctx.cfg.index.exclude)
             and not match_any(f, ctx.cfg.policy.ignore_paths)]
    # Source files first; then others. Stable order.
    files.sort(key=lambda f: (plugin_for(f) is None, f))
    res = ScanResult(rel)
    if len(files) > max_files:
        res.notes.append(f"{len(files)} files; scanned the first {max_files} (source files first)")
        res.files_skipped = len(files) - max_files
        files = files[:max_files]
    for f in files:
        try:
            w = why(ctx, f, rev=rev_sha, deep=deep)
        except (WhyError, GitError):
            res.files_skipped += 1
            continue
        res.files_scanned += 1
        res.decisions += [(f, d) for d in w.decisions if d.type != "cleanup"]
    res.decisions.sort(key=lambda fd: (-fd[1].strength.value, fd[0], fd[1].id))
    return res
