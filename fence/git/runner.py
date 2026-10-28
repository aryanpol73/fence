"""The only place Fence starts a git process.

Every argument that comes from outside (a ref, a path) is untrusted: it may come
from a diff, a CLI argument, or later from model output. So:
- arguments are always a list, never a shell string;
- refs are syntax-checked, then resolved by git itself to a commit SHA;
- paths must stay inside the repo root after resolving symlinks;
- every call has a timeout.
"""

from __future__ import annotations

import codecs
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Iterator, Sequence
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
        # Check the size before reading, so a huge blob in a hostile repo is never loaded.
        size = self.run(["cat-file", "-s", f"{sha}:{rel}"]).strip()
        if not size.isdigit() or int(size) > max_bytes:
            raise GitError(f"file too large to read: {rel} ({size} bytes)")
        return self.run(["show", f"{sha}:{rel}"])

    def read_blobs(
        self, specs: Sequence[tuple[str, str]], *, max_bytes: int = 1_000_000
    ) -> dict[tuple[str, str], str | None]:
        """Read many `(commit_sha, path)` blobs with two git processes total.

        Missing, binary-looking or oversized blobs map to None. Sizes are checked with
        --batch-check first, so oversized content is never transferred.
        """
        ok = [
            (sha, path) for sha, path in dict.fromkeys(specs)
            if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha)
            and path and "\n" not in path and "\r" not in path
        ]
        result: dict[tuple[str, str], str | None] = {s: None for s in specs}
        if not ok:
            return result
        query = "".join(f"{sha}:{path}\n" for sha, path in ok).encode("utf-8")
        checks = _run_bytes(["cat-file", "--batch-check"], cwd=self.root, timeout=self.timeout,
                            input=query).decode("utf-8", "replace").split("\n")
        small = []
        for spec, line in zip(ok, checks, strict=False):
            parts = line.split(" ")
            if len(parts) == 3 and parts[1] == "blob" and parts[2].isdigit():
                if int(parts[2]) <= max_bytes:
                    small.append(spec)
        if not small:
            return result
        query = "".join(f"{sha}:{path}\n" for sha, path in small).encode("utf-8")
        out = _run_bytes(["cat-file", "--batch"], cwd=self.root, timeout=self.timeout,
                         input=query)
        pos = 0
        for spec in small:
            nl = out.index(b"\n", pos)
            header = out[pos:nl].decode("utf-8", "replace").split(" ")
            pos = nl + 1
            if len(header) != 3 or not header[2].isdigit():
                continue
            size = int(header[2])
            data = out[pos : pos + size]
            pos += size + 1  # content is followed by a newline
            if b"\x00" not in data[:8000]:
                result[spec] = data.decode("utf-8", "replace")
        return result

    def stream(
        self, args: Sequence[str], *, timeout: float | None = None, chunk_size: int = 65536
    ) -> Iterator[str]:
        """Run `git <args>` and yield stdout in text chunks, for output too big to hold.

        `timeout` bounds the whole run. Stopping iteration early kills git.
        """
        return _stream(args, cwd=self.root, timeout=timeout or self.timeout, chunk_size=chunk_size)


def _stream(args: Sequence[str], *, cwd: Path, timeout: float, chunk_size: int) -> Iterator[str]:
    if isinstance(args, str):
        raise TypeError("git args must be a list, not a string")
    argv = ["git", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false", *args]
    # stderr goes to a file: a PIPE nobody reads can fill up and deadlock git.
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, env=_git_env(), stdout=subprocess.PIPE, stderr=err, shell=False
            )
        except FileNotFoundError:
            raise GitError("git executable not found") from None
        timed_out = threading.Event()

        def _kill() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(timeout, _kill)
        timer.daemon = True
        timer.start()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            assert proc.stdout is not None  # noqa: S101 - set by stdout=PIPE
            while data := proc.stdout.read1(chunk_size):
                yield decoder.decode(data)
            tail = decoder.decode(b"", final=True)
            if tail:
                yield tail
            returncode = proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()
        if timed_out.is_set():
            raise GitTimeout(f"git {args[0] if args else ''} timed out after {timeout}s")
        if returncode != 0:
            err.seek(0)
            stderr = err.read(MAX_STDERR_CHARS * 4).decode("utf-8", "replace").strip()
            raise GitError(
                f"git {args[0] if args else ''} failed ({returncode}): {stderr[:MAX_STDERR_CHARS]}"
            )


def _run_bytes(
    args: Sequence[str], *, cwd: Path, timeout: float, input: bytes | None = None
) -> bytes:
    if isinstance(args, str):
        raise TypeError("git args must be a list, not a string")
    argv = ["git", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false", *args]
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=_git_env(), input=input, capture_output=True, timeout=timeout,
            check=False, shell=False,
        )
    except subprocess.TimeoutExpired:
        raise GitTimeout(f"git {args[0] if args else ''} timed out after {timeout}s") from None
    except FileNotFoundError:
        raise GitError("git executable not found") from None
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()[:MAX_STDERR_CHARS]
        raise GitError(f"git {args[0] if args else ''} failed ({proc.returncode}): {stderr}")
    return proc.stdout


def _run(args: Sequence[str], *, cwd: Path, timeout: float) -> str:
    # Bytes in, decoded here: text mode would translate CRLF to LF and hand back
    # file content that differs from the blob (and from any diff of it).
    return _run_bytes(args, cwd=cwd, timeout=timeout).decode("utf-8", errors="replace")
