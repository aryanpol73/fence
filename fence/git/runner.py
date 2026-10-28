"""The only place Fence starts a git process.

Every argument that comes from outside (a ref, a path) is untrusted: it may come
from a diff, a CLI argument, or later from model output. So:
- arguments are always a list, never a shell string;
- refs are syntax-checked, then resolved by git itself to a commit SHA;
- paths must stay inside the repo root after resolving symlinks;
- every call has a timeout.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_STDERR_CHARS = 500

# Characters git allows in refs that we also accept: names, SHAs, HEAD~2, main^, origin/main.
_REF_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/~^@{}-]*$")


class GitError(Exception):
    """git failed, or Fence refused to run it."""


class GitTimeout(GitError):
    """git did not finish within the timeout."""


class UnsafeInput(GitError):
    """A ref or path was rejected before reaching git."""


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",  # never block on a credential prompt
            "GIT_PAGER": "cat",
            "GIT_OPTIONAL_LOCKS": "0",  # read-only: don't take index locks
            "LC_ALL": "C",  # stable, parseable output
        }
    )
    return env


@dataclass(frozen=True)
class Repo:
    root: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def open(cls, path: str | Path, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Repo:
        start = Path(path).resolve()
        if not start.is_dir():
            raise GitError(f"not a directory: {start}")
        top = _run(["rev-parse", "--show-toplevel"], cwd=start, timeout=timeout).strip()
        return cls(root=Path(top).resolve(), timeout=timeout)

    def run(self, args: Sequence[str], *, timeout: float | None = None) -> str:
        """Run `git <args>` in the repo root and return stdout."""
        return _run(args, cwd=self.root, timeout=timeout or self.timeout)

    def verify_ref(self, ref: str) -> str:
        """Return the full commit SHA for `ref`, or raise UnsafeInput / GitError."""
        if not ref or not _REF_PATTERN.match(ref) or ".." in ref:
            raise UnsafeInput(f"rejected ref: {ref!r}")
        out = self.run(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"]
        )
        sha = out.strip()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
            raise GitError(f"could not resolve ref: {ref!r}")
        return sha

    def safe_relpath(self, path: str | Path) -> str:
        """Return `path` as a repo-relative POSIX path, refusing anything that escapes the root.

        Works for files that no longer exist in the working tree (deleted in a diff),
        but any symlink that does exist on the way is resolved and checked.
        """
        raw = str(path)
        if not raw or "\x00" in raw or raw.startswith("-"):
            raise UnsafeInput(f"rejected path: {raw!r}")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            rel = resolved.relative_to(self.root)
        except ValueError:
            raise UnsafeInput(f"path escapes repo root: {raw!r}") from None
        rel_posix = PurePosixPath(*rel.parts)
        if str(rel_posix) in ("", "."):
            raise UnsafeInput(f"path is the repo root: {raw!r}")
        return str(rel_posix)

    def read_blob(self, ref: str, path: str, *, max_bytes: int = 1_000_000) -> str:
        """Return the contents of `path` at `ref` (`git show ref:path`)."""
        sha = self.verify_ref(ref)
        rel = self.safe_relpath(path)
        text = self.run(["show", f"{sha}:{rel}"])
        if len(text.encode("utf-8", errors="replace")) > max_bytes:
            raise GitError(f"file too large to read: {rel}")
        return text


def _run(args: Sequence[str], *, cwd: Path, timeout: float) -> str:
    if isinstance(args, str):  # a str is a Sequence too; refuse it so nobody builds shell strings
        raise TypeError("git args must be a list, not a string")
    argv = ["git", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false", *args]
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=_git_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise GitTimeout(f"git {args[0] if args else ''} timed out after {timeout}s") from None
    except FileNotFoundError:
        raise GitError("git executable not found") from None
    if proc.returncode != 0:
        stderr = proc.stderr.strip()[:MAX_STDERR_CHARS]
        raise GitError(f"git {args[0] if args else ''} failed ({proc.returncode}): {stderr}")
    return proc.stdout
