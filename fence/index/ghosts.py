"""Ghost Fences (D2): tombstones for deleted guards, and matching code that comes back.

At index time, every first-parent commit that deletes a guard-like block (guard,
lock, retry/timeout, error handling, assertion) leaves a tombstone: fingerprint,
removing commit, and the deleted lines. When a later change adds code that
matches a tombstone, Fence links the two: "this re-adds a guard removed in
<commit>". The decision the guard carried is resolved lazily, with blame at
the removing commit's parent, only when a match is shown.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Iterator
from dataclasses import dataclass

from fence.anchors.fingerprint import containment, shingles
from fence.check.classify import GUARD_SIGNALS
from fence.check.diff import DiffError, parse_diff
from fence.config import Config
from fence.git.runner import GitError, Repo
from fence.index.store import Store
from fence.lang import plugin_for, tokens_for
from fence.signals.code import match_code
from fence.text import clean

MIN_TOKENS = 8
MATCH = 0.7
MAX_PER_RUN = 5000


@dataclass(frozen=True)
class Ghost:
    tombstone_id: str
    removed_in: str
    path: str
    subject: str
    preview: str
    similarity: float


def _commits(repo: Repo, revs: list[str], timeout: float) -> Iterator[tuple[str, str, str]]:
    """(sha, subject, diff text) for first-parent commits, streamed one at a time."""
    nonce = secrets.token_hex(6)
    marker = f"\x1e{nonce}"
    args = ["log", "--first-parent", "--diff-merges=first-parent", "-p", "--unified=0",
            "--no-color", "--no-ext-diff", "--no-textconv", "-M50%", "--src-prefix=a/",
            "--dst-prefix=b/", f"--format=%x1e{nonce}%H%n%s", *revs, "--"]
    buf = ""
    for chunk in repo.stream(args, timeout=timeout):
        buf += chunk
        parts = buf.split(marker)
        buf = parts.pop()
        for rec in parts:
            if rec.strip():
                yield _split(rec)
    if buf.strip():
        yield _split(buf)


def _split(rec: str) -> tuple[str, str, str]:
    sha, _, rest = rec.partition("\n")
    subject, _, diff = rest.partition("\n")
    return sha.strip(), subject, diff


def scan_tombstones(repo: Repo, store: Store, cfg: Config) -> int:
    """Record tombstones for commits not yet scanned. Returns how many were added."""
    tip = repo.verify_ref(cfg.index.refs[0]) if cfg.index.refs else repo.verify_ref("HEAD")
    done = store.get_meta("ghost_tip")
    revs = [tip] + (["--not", done] if done and _exists(repo, done) else [])
    if done == tip:
        return 0
    added = 0
    rows = []
    for sha, subject, diff in _commits(repo, revs, cfg.performance.log_timeout_seconds):
        if len(diff) > 2_000_000:
            continue
        try:
            files = parse_diff(diff)
        except DiffError:
            continue
        readded = frozenset().union(*(shingles(tokens_for(f.path, "\n".join(f.added_lines)))
                                      for f in files if f.added_lines)) if files else frozenset()
        for f in files:
            if f.old_path is None or plugin_for(f.old_path) is None:
                continue
            for b in f.blocks:
                lines = [t for _, t in b.removed]
                if not {s for s, _ in match_code(lines)} & GUARD_SIGNALS:
                    continue
                toks = tokens_for(f.old_path, "\n".join(lines))
                if len(toks) < MIN_TOKENS:
                    continue
                sh = shingles(toks)
                if containment(sh, readded) >= MATCH:
                    continue  # moved, not deleted
                key = f"{sha}\x00{f.old_path}\x00{b.start}"
                tid = hashlib.sha256(key.encode()).hexdigest()[:16]
                rows.append((tid, sha, f.old_path, None, hashlib.sha256(
                    "\x00".join(toks).encode()).hexdigest()[:24], json.dumps(sorted(sh)),
                    json.dumps({"subject": clean(subject, max_len=200), "start": b.start,
                                "preview": clean("\n".join(lines[:6]), max_len=400)})))
                added += 1
        if added >= MAX_PER_RUN:
            break
    with store.transaction():
        store.conn.executemany("INSERT OR IGNORE INTO tombstones VALUES(?,?,?,?,?,?,?)", rows)
        if added < MAX_PER_RUN:
            store.set_meta("ghost_tip", tip)
    return added


def _exists(repo: Repo, sha: str) -> bool:
    try:
        repo.run(["cat-file", "-e", f"{sha}^{{commit}}"])
        return True
    except GitError:
        return False


def find_ghosts(store: Store, path: str, added_lines: list[str], *,
                exclude_shas: frozenset[str] = frozenset()) -> list[Ghost]:
    """Tombstones whose deleted code is (mostly) re-added by `added_lines`."""
    if not added_lines:
        return []
    added = shingles(tokens_for(path, "\n".join(added_lines)))
    if not added:
        return []
    out = []
    for row in store.conn.execute("SELECT * FROM tombstones"):
        if row["sha"] in exclude_shas:
            continue
        sh = frozenset(json.loads(row["shingles"]))
        sim = containment(sh, added)
        if sim >= MATCH:
            rec = json.loads(row["record"])
            out.append(Ghost(row["id"], row["sha"], row["path"], rec["subject"], rec["preview"],
                             round(sim, 2)))
    out.sort(key=lambda g: (-g.similarity, g.removed_in))
    return out[:5]
