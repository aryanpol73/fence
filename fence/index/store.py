"""The persistent per-repo index: one SQLite file under the cache directory.

Crash safety comes from SQLite itself: WAL journaling, and every indexing batch is
one transaction, so a killed run leaves the last complete batch and nothing half
written. Writes are idempotent upserts, so two concurrent `fence index` runs only
duplicate work; they cannot corrupt each other.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1

# Each entry upgrades the schema by one version. Never edit a shipped entry;
# append a new one.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE commits (
        sha TEXT PRIMARY KEY,
        parents TEXT NOT NULL,            -- space-separated
        author_name TEXT NOT NULL,
        author_email TEXT NOT NULL,
        author_time INTEGER NOT NULL,
        committer_time INTEGER NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        pr_number INTEGER,
        pr_source TEXT,                   -- 'merge-subject' | 'squash-subject' | 'github'
        files_changed INTEGER NOT NULL DEFAULT 0,
        format_only INTEGER NOT NULL DEFAULT 0,
        bulk INTEGER NOT NULL DEFAULT 0,
        orphaned INTEGER NOT NULL DEFAULT 0,
        seq INTEGER NOT NULL              -- indexing order, oldest first
    );
    CREATE INDEX commits_pr ON commits(pr_number);
    CREATE TABLE file_changes (
        sha TEXT NOT NULL,
        path TEXT NOT NULL,
        old_path TEXT,
        status TEXT NOT NULL,             -- A M D R T (git --raw letters)
        similarity INTEGER,
        added INTEGER,                    -- NULL for binary
        deleted INTEGER,
        PRIMARY KEY (sha, path)
    );
    CREATE INDEX file_changes_path ON file_changes(path);
    CREATE INDEX file_changes_old_path ON file_changes(old_path);
    CREATE TABLE signals (
        sha TEXT NOT NULL,
        signal_id TEXT NOT NULL,
        detail TEXT NOT NULL,             -- the matched text, verbatim
        PRIMARY KEY (sha, signal_id, detail)
    );
    CREATE TABLE refs (name TEXT PRIMARY KEY, sha TEXT NOT NULL);
    CREATE TABLE reverts (sha TEXT PRIMARY KEY, reverted_sha TEXT NOT NULL);
    CREATE INDEX reverts_target ON reverts(reverted_sha);
    CREATE TABLE prs (
        number INTEGER PRIMARY KEY,
        title TEXT, body TEXT, labels TEXT, state TEXT, merged_at TEXT,
        merge_sha TEXT, url TEXT, author TEXT, reviewers TEXT, fetched_at INTEGER
    );
    CREATE TABLE issues (
        number INTEGER PRIMARY KEY,
        title TEXT, body TEXT, labels TEXT, state TEXT, closed_at TEXT, url TEXT,
        fetched_at INTEGER, retrieval_status TEXT NOT NULL DEFAULT 'ok'
    );
    CREATE TABLE pr_issue_links (
        pr_number INTEGER NOT NULL, issue_number INTEGER NOT NULL, kind TEXT NOT NULL,
        PRIMARY KEY (pr_number, issue_number)
    );
    CREATE TABLE review_comments (
        id INTEGER PRIMARY KEY, pr_number INTEGER NOT NULL, path TEXT, line INTEGER,
        body TEXT, author TEXT, url TEXT, created_at TEXT
    );
    CREATE INDEX review_comments_pr ON review_comments(pr_number);
    CREATE TABLE http_cache (url TEXT PRIMARY KEY, etag TEXT, body TEXT, fetched_at INTEGER);
    CREATE TABLE query_cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at INTEGER);
    CREATE TABLE tombstones (
        id TEXT PRIMARY KEY, sha TEXT NOT NULL, path TEXT NOT NULL, symbol TEXT,
        fingerprint TEXT NOT NULL, shingles TEXT NOT NULL, record TEXT NOT NULL
    );
    """,
)


class IndexUnavailable(Exception):
    """The index is missing, corrupt, or from a newer Fence."""


@dataclass(frozen=True)
class CommitRow:
    sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    author_time: int
    committer_time: int
    subject: str
    body: str
    pr_number: int | None = None
    pr_source: str | None = None
    files_changed: int = 0
    format_only: bool = False
    bulk: bool = False
    orphaned: bool = False

    @property
    def message(self) -> str:
        return f"{self.subject}\n\n{self.body}".strip() if self.body else self.subject


@dataclass(frozen=True)
class FileChange:
    sha: str
    path: str
    old_path: str | None
    status: str
    similarity: int | None
    added: int | None
    deleted: int | None


def db_path_for(cache_dir: Path, repo_root: Path) -> Path:
    key = hashlib.sha256(str(repo_root.resolve()).lower().encode()).hexdigest()[:16]
    return cache_dir / "index" / f"{repo_root.name[:40]}-{key}.sqlite3"


class Store:
    def __init__(self, conn: sqlite3.Connection, path: Path | None) -> None:
        self.conn = conn
        self.path = path

    @classmethod
    def open(cls, path: Path | str, *, create: bool = True) -> Store:
        if str(path) != ":memory:":
            p = Path(path)
            if not p.exists() and not create:
                raise IndexUnavailable(f"no index at {p}; run `fence index` first")
            p.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            store = cls(conn, None if str(path) == ":memory:" else Path(path))
            store._migrate()
        except sqlite3.DatabaseError as e:
            raise IndexUnavailable(
                f"index at {path} is unreadable ({e}); run `fence reindex`"
            ) from None
        return store

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        has_meta = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        version = int(self.get_meta("schema_version") or 0) if has_meta else 0
        if version > SCHEMA_VERSION:
            raise IndexUnavailable(
                f"index schema v{version} is newer than this Fence (v{SCHEMA_VERSION}); upgrade"
            )
        for target in range(version + 1, SCHEMA_VERSION + 1):
            with self.transaction():
                for stmt in _split_sql(MIGRATIONS[target - 1]):
                    self.conn.execute(stmt)
                self.set_meta("schema_version", str(target))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE ... COMMIT, rolled back on any exception."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    # meta -------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # commits ----------------------------------------------------------------

    def next_seq(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM commits").fetchone()
        return int(row["n"])

    def put_commits(self, commits: Iterable[CommitRow], changes: Iterable[FileChange]) -> None:
        """Upsert a batch. Call inside transaction()."""
        seq = self.next_seq()
        for c in commits:
            self.conn.execute(
                "INSERT INTO commits(sha, parents, author_name, author_email, author_time,"
                " committer_time, subject, body, pr_number, pr_source, files_changed,"
                " format_only, bulk, orphaned, seq) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)"
                " ON CONFLICT(sha) DO UPDATE SET orphaned = 0",
                (
                    c.sha, " ".join(c.parents), c.author_name, c.author_email, c.author_time,
                    c.committer_time, c.subject, c.body, c.pr_number, c.pr_source,
                    c.files_changed, int(c.format_only), int(c.bulk), seq,
                ),
            )
            seq += 1
        self.conn.executemany(
            "INSERT OR REPLACE INTO file_changes VALUES(?,?,?,?,?,?,?)",
            [
                (f.sha, f.path, f.old_path, f.status, f.similarity, f.added, f.deleted)
                for f in changes
            ],
        )

    def put_signals(self, rows: Iterable[tuple[str, str, str]]) -> None:
        self.conn.executemany("INSERT OR IGNORE INTO signals VALUES(?,?,?)", list(rows))

    def put_revert(self, sha: str, reverted_sha: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO reverts VALUES(?,?)", (sha, reverted_sha))

    def has_commit(self, sha: str) -> bool:
        return self.conn.execute("SELECT 1 FROM commits WHERE sha=?", (sha,)).fetchone() is not None

    def commit(self, sha: str) -> CommitRow | None:
        row = self.conn.execute("SELECT * FROM commits WHERE sha = ?", (sha,)).fetchone()
        return _commit_row(row) if row else None

    def commits(self, shas: Iterable[str]) -> dict[str, CommitRow]:
        shas = list(dict.fromkeys(shas))
        out: dict[str, CommitRow] = {}
        for chunk in _chunks(shas, 500):
            marks = ",".join("?" * len(chunk))
            for row in self.conn.execute(f"SELECT * FROM commits WHERE sha IN ({marks})", chunk):  # noqa: S608
                out[row["sha"]] = _commit_row(row)
        return out

    def commit_count(self, *, include_orphaned: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM commits"
        if not include_orphaned:
            sql += " WHERE orphaned = 0"
        return int(self.conn.execute(sql).fetchone()["n"])

    def signals_for(self, sha: str) -> list[tuple[str, str]]:
        return [
            (r["signal_id"], r["detail"])
            for r in self.conn.execute(
                "SELECT signal_id, detail FROM signals WHERE sha = ? ORDER BY signal_id, detail",
                (sha,),
            )
        ]

    def reverts_of(self, sha: str, *, as_of: int | None = None) -> list[str]:
        """Commits that revert `sha` (committed at or before `as_of`, if given)."""
        return [
            r["sha"]
            for r in self.conn.execute(
                "SELECT r.sha FROM reverts r JOIN commits c ON c.sha = r.sha "
                "WHERE r.reverted_sha = ? AND c.committer_time <= ? ORDER BY c.seq",
                (sha, as_of if as_of is not None else 2**62),
            )
        ]

    def reverted_by(self, sha: str) -> str | None:
        row = self.conn.execute("SELECT reverted_sha FROM reverts WHERE sha = ?", (sha,)).fetchone()
        return row["reverted_sha"] if row else None

    def changes_for(self, sha: str) -> list[FileChange]:
        return [
            FileChange(**dict(r))
            for r in self.conn.execute(
                "SELECT * FROM file_changes WHERE sha = ? ORDER BY path", (sha,)
            )
        ]

    def path_history(self, path: str, *, limit: int = 50) -> list[str]:
        """Earlier names of `path`, following renames backwards, oldest last."""
        names = [path]
        seen = {path}
        current = path
        for _ in range(limit):
            row = self.conn.execute(
                "SELECT fc.old_path FROM file_changes fc JOIN commits c ON c.sha = fc.sha"
                " WHERE fc.path = ? AND fc.status = 'R' AND fc.old_path IS NOT NULL"
                " AND c.orphaned = 0 ORDER BY c.seq DESC LIMIT 1",
                (current,),
            ).fetchone()
            if not row or row["old_path"] in seen:
                break
            current = row["old_path"]
            names.append(current)
            seen.add(current)
        return names

    # refs and orphan detection ------------------------------------------------

    def refs(self) -> dict[str, str]:
        return {r["name"]: r["sha"] for r in self.conn.execute("SELECT name, sha FROM refs")}

    def set_ref(self, name: str, sha: str) -> None:
        self.conn.execute(
            "INSERT INTO refs VALUES(?, ?) ON CONFLICT(name) DO UPDATE SET sha = excluded.sha",
            (name, sha),
        )

    def mark_orphans(self, reachable: Iterable[str]) -> int:
        """Mark every commit not in `reachable` as orphaned. Returns how many changed."""
        self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS reach (sha TEXT PRIMARY KEY)")
        self.conn.execute("DELETE FROM reach")
        for chunk in _chunks(list(reachable), 5000):
            self.conn.executemany("INSERT OR IGNORE INTO reach VALUES(?)", [(s,) for s in chunk])
        cur = self.conn.execute(
            "UPDATE commits SET orphaned = 1 WHERE orphaned = 0 "
            "AND sha NOT IN (SELECT sha FROM reach)"
        )
        self.conn.execute("UPDATE commits SET orphaned = 0 WHERE sha IN (SELECT sha FROM reach)")
        return cur.rowcount

    # query cache ------------------------------------------------------------

    def cache_get(self, key: str) -> object | None:
        row = self.conn.execute("SELECT value FROM query_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def cache_put(self, key: str, value: object) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO query_cache VALUES(?, ?, ?)",
            (key, json.dumps(value, sort_keys=True), int(time.time())),
        )

    def clear_query_cache(self) -> None:
        self.conn.execute("DELETE FROM query_cache")

    # health -----------------------------------------------------------------

    def integrity_ok(self) -> bool:
        row = self.conn.execute("PRAGMA quick_check").fetchone()
        return bool(row) and row[0] == "ok"


def _commit_row(row: sqlite3.Row) -> CommitRow:
    return CommitRow(
        sha=row["sha"],
        parents=tuple(p for p in row["parents"].split(" ") if p),
        author_name=row["author_name"],
        author_email=row["author_email"],
        author_time=row["author_time"],
        committer_time=row["committer_time"],
        subject=row["subject"],
        body=row["body"],
        pr_number=row["pr_number"],
        pr_source=row["pr_source"],
        files_changed=row["files_changed"],
        format_only=bool(row["format_only"]),
        bulk=bool(row["bulk"]),
        orphaned=bool(row["orphaned"]),
    )


def _split_sql(script: str) -> list[str]:
    # Migration scripts are ours and contain no ';' inside literals, so a plain
    # split is enough, and it lets each statement run inside our transaction
    # (executescript would commit implicitly).
    return [s.strip() for s in script.split(";") if s.strip()]


def _chunks(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
